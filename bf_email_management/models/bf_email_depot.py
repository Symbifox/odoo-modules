# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Le courriel qu'on ne reçoit plus, rangé quand même dans sa boîte.

Un collègue qui lit ses avis dans Odoo (`notification_type = 'inbox'`) ne reçoit
plus de courriel : c'est le but, et le composeur de ce module l'applique déjà
(`_bf_abonnes_notifiables`). Mais ce qui n'arrive plus par courriel **n'est plus
cherchable dans son logiciel de courrier**, et c'est là que la plupart des gens
cherchent six mois plus tard.

D'où ce dépôt : le message est écrit directement dans un dossier IMAP de la
personne, par `APPEND`. Rien ne transite par SMTP, donc aucun envoi, aucun saut
externe, aucun quota consommé, et aucun rebond possible. La copie est posée
**déjà lue** et dans un dossier à part, pour archiver sans faire sonner quoi que
ce soit.

🔴 **Jamais dans la transaction de la personne qui écrit.** Un `APPEND` est un
aller-retour réseau : posé dans `_notify_thread_by_inbox`, il ferait attendre
l'auteur du message, et un serveur IMAP lent ou muet ferait échouer un simple
commentaire au chatter. On met donc en file ici, et un cron dépose.

⚠️ **Le dépôt n'est pas un envoi**, et rien ne doit le prendre pour tel : il ne
crée ni `mail.mail`, ni `bf.email`, ni ligne d'historique chez un contact. Il ne
laisse qu'une ligne dans cette file.
"""
import logging
from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

from . import bf_email_imap

_logger = logging.getLogger(__name__)

# L'en-tête qui dit ce que la copie est, pour qui la retrouve dans son client.
ENTETE_DEPOT = "X-BF-Depot-Odoo"
# Au plus par passe : un dépôt est une connexion IMAP, pas une requête SQL.
PAR_PASSE = 200
# Au plus de passes qui trouvent la boîte injoignable avant d'abandonner une
# ligne. Au rythme du cron (dix minutes), environ deux heures : une panne
# passagère se rattrape, un mot de passe changé ou un jeton expiré ne garde
# pas la ligne en file pour toujours.
PLAFOND_TENTATIVES = 12
# Le ménage : une ligne terminée (déposée, sans objet, en échec) reste ce
# nombre de jours pour qu'on puisse la consulter, puis part. Au plus
# PURGE_PAR_PASSE lignes par passe, pour qu'une file longtemps négligée se
# vide en plusieurs passes plutôt qu'en une transaction démesurée.
GARDE_JOURS = 30
PURGE_PAR_PASSE = 1000
ETATS_TERMINAUX = ("done", "skipped", "error")


class BfEmailDepot(models.Model):
    _name = "bf.email.depot"
    _description = "Avis Odoo à déposer dans la boîte IMAP de son destinataire"
    _order = "id"
    _rec_name = "message_id"

    user_id = fields.Many2one(
        "res.users", string="Destinataire", required=True, index=True,
        ondelete="cascade")
    message_id = fields.Many2one(
        "mail.message", string="Message", required=True, index=True,
        ondelete="cascade")
    state = fields.Selection(
        [("pending", "À déposer"), ("done", "Déposé"),
         ("skipped", "Sans objet"), ("error", "En échec")],
        default="pending", required=True, index=True)
    error = fields.Char(string="Dernier échec", readonly=True)
    attempts = fields.Integer(string="Tentatives", default=0, readonly=True)
    deposited_on = fields.Datetime(string="Déposé le", readonly=True)

    _sql_constraints = [
        # Un message, un destinataire, une copie. Le noyau peut rappeler
        # `_notify_thread_by_inbox` (reprise d'un envoi, deuxième passe d'un
        # cron) : sans cette contrainte, la boîte prendrait des doublons.
        ("message_user_uniq", "unique (message_id, user_id)",
         "Ce message est déjà en file pour cette personne."),
    ]

    # ------------------------------------------------------------------
    # Mise en file
    # ------------------------------------------------------------------

    @api.model
    def _bf_mettre_en_file(self, message, partenaires):
        """Met en file ce message pour ceux qui ont demandé le dépôt.

        Rend les lignes créées. ⚠️ Ne lève jamais : une file qui refuse ne doit
        pas faire échouer la publication du message.
        """
        if not message or not partenaires:
            return self.browse()
        try:
            usagers = partenaires.sudo().user_ids.filtered(
                lambda u: not u.share and u.active and u.bf_depot_imap_avis)
            if not usagers:
                return self.browse()
            deja = set(self.sudo().search([
                ("message_id", "=", message.id),
                ("user_id", "in", usagers.ids),
            ]).mapped("user_id.id"))
            a_creer = [{"user_id": u.id, "message_id": message.id}
                       for u in usagers if u.id not in deja]
            if not a_creer:
                return self.browse()
            return self.sudo().create(a_creer)
        except Exception as e:  # noqa: BLE001 - journalisé, jamais bloquant
            _logger.warning(
                "Dépôt IMAP non mis en file pour le message %s : %s",
                message.id if message else None, e)
            return self.browse()

    # ------------------------------------------------------------------
    # Le dépôt
    # ------------------------------------------------------------------

    def _bf_compte(self):
        """Le compte IMAP où déposer, ou rien.

        Le dossier propre de la personne (`own_inbox`), parce qu'un dépôt dans
        une boîte partagée serait un courriel de plus pour tout le monde.
        """
        self.ensure_one()
        return self.env["bf.email.account"].sudo().search([
            ("user_id", "=", self.user_id.id),
            ("own_inbox", "=", True),
            ("active", "=", True),
        ], limit=1)

    def _bf_mime(self):
        """Le message MIME à déposer.

        Reconstruit depuis le `mail.message` : ce que la personne aurait reçu,
        sans l'habillage d'envoi. Le corps part en Html, avec une version texte
        de repli, parce qu'un client qui n'affiche pas le Html montrerait une
        page blanche.
        """
        self.ensure_one()
        message = self.message_id.sudo()
        mime = EmailMessage()
        auteur = message.author_id
        expediteur = (message.email_from
                      or (formataddr((auteur.name or "", auteur.email))
                          if auteur and auteur.email else False)
                      or self.env.company.email
                      or "odoo@localhost")
        mime["From"] = expediteur
        mime["To"] = formataddr(
            (self.user_id.partner_id.name or "", self.user_id.email or ""))
        mime["Subject"] = (message.subject
                           or message.record_name
                           or _("Avis Odoo"))
        mime["Date"] = format_datetime(
            fields.Datetime.context_timestamp(self, message.date or fields.Datetime.now()))
        # ⚠️ On garde l'identité du message d'Odoo quand il en a une : c'est
        # elle qui permet à un client de rattacher la copie au même fil.
        mime["Message-Id"] = message.message_id or make_msgid()
        mime[ENTETE_DEPOT] = "1"
        texte = self.env["mail.render.mixin"].sudo()._replace_local_links(
            message.body or "")
        mime.set_content(html2plaintext(texte) if texte
                         else _("Voir le message dans Odoo."))
        mime.add_alternative(texte or "", subtype="html")
        return mime

    @api.model
    def _bf_purger(self):
        """Jette les lignes terminées depuis plus de GARDE_JOURS jours.

        Seulement les états terminaux : une ligne encore à déposer ne part
        jamais par ici. Borné à PURGE_PAR_PASSE lignes, les plus anciennes
        d'abord. Rend le nombre de lignes jetées.

        ⚠️ Appelé par le cron du dépôt lui-même, pas par `@api.autovacuum` :
        le cron du ménage général n'est pas actif sur toutes les bases.
        """
        limite = fields.Datetime.now() - timedelta(days=GARDE_JOURS)
        vieilles = self.sudo().search([
            ("state", "in", ETATS_TERMINAUX),
            ("write_date", "<", limite),
        ], order="write_date, id", limit=PURGE_PAR_PASSE)
        nombre = len(vieilles)
        if vieilles:
            vieilles.unlink()
        return nombre

    def _bf_noter_injoignable(self, raison):
        """Compte une tentative sur chaque ligne d'une boîte injoignable.

        Chaque ligne garde son propre compte. Celle qui atteint
        PLAFOND_TENTATIVES passe « En échec » et sort de la file, avec la
        raison du dernier échec ; les autres restent à déposer.
        """
        raison = str(raison)[:200]
        for ligne in self:
            tentatives = ligne.attempts + 1
            vals = {"error": raison, "attempts": tentatives}
            if tentatives >= PLAFOND_TENTATIVES:
                vals["state"] = "error"
                vals["error"] = _(
                    "Abandon après %(n)s tentatives : %(raison)s",
                    n=tentatives, raison=raison)
            ligne.write(vals)

    @api.model
    def _cron_deposer(self, limite=PAR_PASSE):
        """Dépose les avis en file, un compte à la fois.

        Une connexion par personne, pas par message : c'est la connexion qui
        coûte. Un échec marque la ligne et laisse les autres passer.

        🔴 Les lignes jamais tentées passent d'abord (`attempts`, puis `id`).
        Une boîte injoignable garde ses lignes en file jusqu'au plafond ; triées
        par `id` seulement, il suffirait qu'elle en accumule `limite` pour que
        chaque passe ne voie plus qu'elles et que plus personne ne reçoive rien.
        """
        self._bf_purger()
        a_faire = self.sudo().search(
            [("state", "=", "pending")], order="attempts, id", limit=limite)
        if not a_faire:
            return 0
        deposes = 0
        for usager in a_faire.mapped("user_id"):
            lignes = a_faire.filtered(lambda l: l.user_id == usager)
            compte = lignes[0]._bf_compte()
            if not compte:
                lignes.write({
                    "state": "skipped",
                    "error": "aucune boîte IMAP propre et active",
                })
                continue
            dossier = (compte.bf_depot_imap_folder or "").strip() or "Odoo"
            conn = None
            try:
                conn = compte._ouvrir_imap()
                bf_email_imap.ensure_folder(conn, dossier)
                for ligne in lignes:
                    try:
                        mime = ligne._bf_mime()
                        # ⚠️ Déposé DÉJÀ LU : la personne lit l'avis dans Odoo,
                        # la copie est une archive, pas une seconde sonnerie.
                        conn.append(
                            bf_email_imap.imap_quote_mailbox(dossier),
                            r"(\Seen)", None, mime.as_bytes())
                        ligne.write({
                            "state": "done",
                            "error": False,
                            "deposited_on": fields.Datetime.now(),
                            "attempts": ligne.attempts + 1,
                        })
                        deposes += 1
                    except Exception as e:  # noqa: BLE001
                        _logger.warning("Dépôt IMAP en échec (ligne %s) : %s",
                                        ligne.id, e)
                        ligne.write({"state": "error", "error": str(e)[:200],
                                     "attempts": ligne.attempts + 1})
            except Exception as e:  # noqa: BLE001
                _logger.warning(
                    "Boîte IMAP de %s injoignable, dépôts remis à plus tard : %s",
                    usager.login, e)
                lignes._bf_noter_injoignable(e)
            finally:
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:  # noqa: BLE001
                        pass
        return deposes
