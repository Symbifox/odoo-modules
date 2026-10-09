"""Le vieux courriel qui apparaît dans la boîte : retenu, pas ingéré.

**Ce qui est retenu.** Sur les seuls chemins automatiques (synchro vive de INBOX
et Sent, passe du cron de réconciliation), un Message-ID jamais vu pour ce
propriétaire dont l'en-tête ``Date`` a plus de ``bf_email.ingest_max_age_days``
jours (30 par défaut, la fenêtre de la réconciliation). Un rattrapage voulu
(assistant, réconciliation lancée avec ``days``, navigateur IMAP) ne pose pas
le contexte de garde et passe.

**Ce qui n'est pas touché.** Le serveur : ni déplacement, ni drapeau. La ligne
retenue ne garde que de quoi retrouver le message et décider (compte, dossier,
UID, Message-ID, date, expéditeur, objet).

**La décision** se prend par lot (compte et dossier) : un avis groupé à la fin
de la passe, et le dossier « À décider » de la boîte tant qu'un lot attend.
« Ajouter » ingère en arrière-plan, par paquets, sans avis d'arrivée, règles
appliquées en local seulement (catégories oui, aucun renvoi IMAP ni courriel
sortant). « Ignorer » est mémorisé : ces Message-ID ne sont plus jamais
redemandés.

⚠️ Un courriel sans en-tête ``Date`` lisible n'est jamais retenu : la date
interne IMAP coûterait un aller-retour par message, et un tel courriel est rare.
"""
import logging
from datetime import timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import bf_email_imap

_logger = logging.getLogger(__name__)

HELD_CHANNEL = "bf_email/held"
INGEST_BATCH = 200


class BfEmailHeld(models.Model):
    _name = "bf.email.held"
    _description = "Ancien courriel retenu à l'ingestion"
    _order = "date desc, id desc"

    account_id = fields.Many2one(
        "bf.email.account", string="Compte", required=True, index=True,
        ondelete="cascade")
    user_id = fields.Many2one(
        "res.users", string="Propriétaire", related="account_id.user_id",
        store=True, index=True)
    folder = fields.Char(string="Dossier", required=True)
    uid = fields.Integer(string="UID IMAP")
    message_id = fields.Char(string="Message-ID", required=True, index=True)
    date = fields.Datetime(string="Date du courriel")
    email_from = fields.Char(string="Expéditeur")
    subject = fields.Char(string="Objet")
    size = fields.Integer(string="Taille (octets)")
    state = fields.Selection(
        [("pending", "À décider"), ("queued", "À ajouter"),
         ("added", "Ajouté"), ("ignored", "Ignoré"), ("gone", "Introuvable")],
        string="État", required=True, default="pending", index=True)
    announced = fields.Boolean(string="Annoncé", default=False)

    _sql_constraints = [
        ("account_message_uniq", "unique(account_id, message_id)",
         "Un même courriel n'est retenu qu'une fois par compte."),
    ]

    # ------------------------------------------------------------------
    # La garde
    # ------------------------------------------------------------------
    @api.model
    def _max_age_days(self):
        try:
            return int(self.env["ir.config_parameter"].sudo().get_param(
                "bf_email.ingest_max_age_days", "30"))
        except (TypeError, ValueError):
            return 30

    @api.model
    def _hold_if_old(self, msg, raw_bytes, uid, folder, account, message_id):
        """``True`` quand ce courriel est (ou reste) retenu et ne doit pas être
        ingéré. ``False`` laisse l'ingestion suivre son cours."""
        seuil = self._max_age_days()
        if seuil <= 0:
            return False
        try:
            quand = parsedate_to_datetime(str(msg.get("Date", "")))
        except (TypeError, ValueError, IndexError):
            quand = None
        if not quand:
            return False
        if quand.tzinfo is not None:
            quand = quand.astimezone(timezone.utc).replace(tzinfo=None)
        if quand > fields.Datetime.now() - timedelta(days=seuil):
            return False
        Held = self.sudo()
        deja = Held.search([
            ("account_id", "=", account.id), ("message_id", "=", message_id),
        ], limit=1)
        if deja:
            # Relecture adverse : TOUJOURS dehors. La passe d'ajout n'a pas
            # besoin d'exception (elle ne pose pas le contexte de garde) ; en
            # rendre une à « À ajouter » laissait la réconciliation ingérer le
            # lot elle-même, avec renvois, réponses d'absence et déplacements.
            if deja.state == "gone":
                # Introuvable à l'ajout, puis revu ailleurs : la décision se
                # repose, au nouvel endroit.
                deja.write({"state": "pending", "folder": folder or "INBOX",
                            "uid": int(uid) if uid else 0, "announced": False})
            return True
        _nom, adresse = parseaddr(str(msg.get("From", "")))
        Held.create({
            "account_id": account.id,
            "folder": folder or "INBOX",
            "uid": int(uid) if uid else 0,
            "message_id": message_id,
            "date": quand,
            "email_from": (str(msg.get("From", "")) or adresse)[:255],
            # `parse_rfc822` lit avec `policy.default` : l'en-tête est déjà
            # décodé (=?utf-8?…?=).
            "subject": str(msg.get("Subject", ""))[:255],
            "size": len(raw_bytes or b""),
        })
        _logger.info(
            "bf.email : courriel de %s retenu (%s, %s, UID %s) : plus vieux que "
            "%s jours", quand.date(), account.display_name, folder, uid, seuil)
        return True

    # ------------------------------------------------------------------
    # L'avis
    # ------------------------------------------------------------------
    @api.model
    def _groups(self, domain):
        """Lots (compte, dossier) : compte, dates extrêmes.

        ``_read_group`` et non ``read_group`` : en 18.0 le second ne rend pas
        de clé ``date:min``, et l'avis tombait sur une ``KeyError`` avalée.
        """
        lots = []
        for compte, dossier, debut, fin, nombre in self.sudo()._read_group(
                domain, ["account_id", "folder"],
                ["date:min", "date:max", "__count"]):
            lots.append({
                "account_id": compte.id,
                "account": compte.display_name,
                "folder": dossier,
                "count": nombre,
                "date_min": fields.Date.to_string(debut) if debut else False,
                "date_max": fields.Date.to_string(fin) if fin else False,
            })
        return lots

    @api.model
    def _announce(self, account):
        """Un avis par lot neuf, au propriétaire, sauf en « ne pas déranger » :
        la décision attend alors dans « À décider » et l'avis part à la
        passe suivante. Ne lève jamais."""
        try:
            owner = account.user_id
            if not owner:
                return
            domaine = [("account_id", "=", account.id), ("state", "=", "pending"),
                       ("announced", "=", False)]
            neufs = self.sudo().search(domaine)
            if not neufs:
                return
            if "bf.dnd" in self.env:
                try:
                    if self.env["bf.dnd"].sudo()._active_for(owner):
                        return
                except Exception:  # noqa: BLE001 - un DND illisible n'empêche rien
                    _logger.debug("bf.email held : état DND illisible", exc_info=True)
            lots = self._groups(domaine)
            neufs.write({"announced": True})
            self.env["bus.bus"].sudo()._sendone(
                owner.partner_id, HELD_CHANNEL, {"lots": lots})
        except Exception:  # noqa: BLE001 - ne jamais casser l'ingestion
            _logger.warning("bf.email held : avis en échec", exc_info=True)

    # ------------------------------------------------------------------
    # La décision (appelée par la boîte)
    # ------------------------------------------------------------------
    @api.model
    def held_summary(self):
        """Les lots qui attendent une décision, pour l'usager courant, et le
        seuil réglé (``bf_email.ingest_max_age_days``) : le dossier
        « À décider » affichait « plus de 30 jours » en dur, quel que soit le
        réglage."""
        return {"lots": self._groups(self._mine_pending()),
                "max_age_days": self._max_age_days()}

    @api.model
    def held_count(self):
        return self.sudo().search_count(self._mine_pending())

    @api.model
    def _mine_pending(self):
        # Un compte désactivé ne se relève plus : son lot ne s'ajouterait
        # jamais, il n'a rien à proposer.
        return [("user_id", "=", self.env.uid), ("state", "=", "pending"),
                ("account_id.active", "=", True)]

    @api.model
    def held_decide(self, account_id, folder, decision):
        """« add » ou « ignore » sur un lot de l'usager courant. Rend le nombre
        de courriels touchés."""
        if decision not in ("add", "ignore"):
            raise UserError(_("Décision inconnue : %s", decision))
        compte = self.env["bf.email.account"].sudo().browse(int(account_id)).exists()
        if not compte or compte.user_id != self.env.user:
            raise AccessError(_("Ce lot n'est pas le vôtre."))
        if not compte.active:
            raise UserError(_("Le compte %s est désactivé : rien ne peut en "
                              "être relevé.", compte.display_name))
        lot = self.sudo().search([
            ("account_id", "=", compte.id), ("folder", "=", folder),
            ("state", "=", "pending"),
        ])
        if not lot:
            return 0
        if decision == "ignore":
            lot.write({"state": "ignored"})
        else:
            lot.write({"state": "queued"})
            self.env.ref("bf_email_management.ir_cron_bf_email_held_ingest")._trigger()
        return len(lot)

    # ------------------------------------------------------------------
    # L'ajout, en arrière-plan
    # ------------------------------------------------------------------
    @api.model
    def _cron_ingest_queued(self):
        """Ingère les lots « À ajouter », par paquets de 200.

        Sans garde (c'est la décision), règles en local seulement, aucun avis
        d'arrivée (on ne passe pas par ``_sync_account``). Le message est
        cherché à son UID, puis par Message-ID dans son dossier ; absent, la
        ligne passe « Introuvable » (et redevient « À décider » si la garde le
        revoit ailleurs).

        Relecture adverse : un échec de connexion ou de sélection rend le lot
        « À décider » au lieu de le laisser « À ajouter », et le cron ne se
        relance que s'il a avancé. Sans ça, un compte au mot de passe changé
        était relancé sans pause jusqu'au verrouillage chez le fournisseur.
        """
        Held = self.sudo()
        attente = Held.search([("state", "=", "queued")], limit=INGEST_BATCH, order="id")
        if not attente:
            return 0
        faits = 0
        par_lot = {}
        for ligne in attente:
            par_lot.setdefault((ligne.account_id, ligne.folder), Held.browse())
            par_lot[(ligne.account_id, ligne.folder)] |= ligne
        for (compte, dossier), lignes in par_lot.items():
            if not compte.active:
                lignes.write({"state": "pending"})
                continue
            try:
                conn = compte._ouvrir_imap()
            except Exception as exc:  # noqa: BLE001 - jamais de relance en boucle
                _logger.warning("bf.email held (%s) : %s ; lot rendu à décider",
                                compte.display_name, exc)
                lignes.write({"state": "pending"})
                continue
            try:
                if not bf_email_imap.select_folder(conn, dossier, readonly=True):
                    lignes.write({"state": "pending"})
                    continue
                owner_env = self.env["bf.email"].with_user(compte.user_id).with_context(
                    allowed_company_ids=(
                        compte.company_id or compte.user_id.company_id).ids,
                    bf_email_rules_local_only=True,
                ).env
                BfEmail = owner_env["bf.email"]
                for ligne in lignes:
                    uid = ligne.uid
                    if not (uid and bf_email_imap.uid_carries_message_id(
                            conn, str(uid), ligne.message_id)):
                        trouve = self.env["bf.email"]._imap_find_uid(
                            conn, ligne.message_id)
                        uid = int(trouve) if trouve else 0
                    raw = bf_email_imap.fetch_rfc822(conn, uid) if uid else None
                    if not raw:
                        ligne.state = "gone"
                        continue
                    try:
                        with self.env.cr.savepoint():
                            BfEmail._ingest_rfc822(raw, uid, dossier, compte)
                        ligne.state = "added"
                        faits += 1
                    except Exception:
                        _logger.warning(
                            "bf.email held : ajout en échec (UID %s, %r)",
                            uid, dossier, exc_info=True)
                        ligne.state = "pending"
            except Exception:  # noqa: BLE001 - un lot ne fait pas tomber les autres
                _logger.warning("bf.email held (%s, %r) : passe interrompue",
                                compte.display_name, dossier, exc_info=True)
                lignes.filtered(lambda l: l.state == "queued").write({"state": "pending"})
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        if faits and Held.search_count([("state", "=", "queued")], limit=1):
            self.env.ref("bf_email_management.ir_cron_bf_email_held_ingest")._trigger()
        _logger.info("bf.email held : %s courriel(s) ajouté(s)", faits)
        return faits
