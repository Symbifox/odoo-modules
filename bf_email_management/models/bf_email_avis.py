"""Les envois automatiques dans l'historique d'un contact.

Ce qu'Odoo envoie de lui-même (lien d'un transfert sécurisé, sondage de
rendez-vous, signature, avis d'assignation à un externe) ne passait
pas par ``bf.email`` : la fiche d'un contact n'en montrait rien. Leurs
destinataires ne sont tracés que dans le ``mail.mail``, qu'Odoo supprime après
l'envoi.

**La capture se fait donc à l'envoi**, dans ``_postprocess_sent_message``,
juste avant la suppression : c'est le seul moment où les adresses existent
encore toutes. Le passé n'est rattrapé qu'en partie (ce qui garde un
destinataire).

**Un avis n'est pas un envoi.** Il porte sa propre direction, ``notice`` :
tout ce qui teste ``direction == "out"`` (Envoyés, Relance à faire, le
tableau de bord, les délais de réponse, la recherche ``est:envoyé``) l'écarte
de lui-même, et tout ce qui teste ``"in"`` aussi (Non lus, À répondre). Il
naît lu et traité : jamais dans la boîte. Les règles automatiques ne s'y
appliquent pas (un renvoi automatique d'un avis serait un envoi de plus), et
le téléphone ne le reçoit pas (l'appli ne connaît que in et out).

**À qui il appartient** : à l'usager interne qui l'a
déclenché ; envoyé par Gen, par un visiteur ou par une tâche planifiée, au
responsable de la fiche d'origine (son ``user_id``) ; à défaut, au compte de
repli ``bf_email.avis_repli_user_id``, par défaut l'administrateur principal.
Même esprit que « chacun sa boîte ».

**Ce qui n'en est jamais un** : une discussion (sous-type « Discussions »,
même postée en ``notification``), un courriel de compte (``res.users`` : lien
de mot de passe à jeton), un envoi sans fiche déclenché par un visiteur (code à
usage unique), une infolettre (``mailing_id``), un envoi qui ne touche que des
collègues.

**Jamais en double.** Une partie de ces messages reçoit aussi, plus tard, une
copie ordinaire (chatter, passerelle, rattrapage), de quelques secondes à
plusieurs semaines après l'envoi. La capture ne crée rien si une copie existe
déjà pour ce message et cet usager, et une copie qui arrive ensuite remplace
l'avis.
"""
import logging

from odoo import api, fields, models
from odoo.tools import email_normalize_all

_logger = logging.getLogger(__name__)

AVIS = "notice"
# Les types que la projection du chatter ne copie pas d'elle-même. « email » et
# « comment » ont leur propre chemin (`_cron_sync_emails`) : les capturer ici
# ferait double emploi.
TYPES_AVIS = ("email_outgoing", "notification", "user_notification", "auto_comment")
# Les courriels de compte : lien de mot de passe, invitation au portail (lien
# d'inscription valide 144 h), deuxième facteur. Jamais gardés chez un autre.
MODELES_DE_COMPTE = ("res.users", "portal.wizard", "portal.wizard.user")


class BfEmailAvis(models.Model):
    _inherit = "bf.email"

    direction = fields.Selection(
        selection_add=[(AVIS, "Avis automatique")],
        ondelete={AVIS: "cascade"},
    )
    bf_avis_sans_corps = fields.Boolean(
        string="Enveloppe seulement", readonly=True, copy=False,
        help="Avis d'un courriel qu'Odoo supprime exprès avec son corps après "
             "l'envoi (invitation à signer, sondage) : le corps porte "
             "souvent un lien à jeton. L'avis n'en garde que l'enveloppe.")

    @api.depends("mail_message_id.body", "raw_rfc822", "source", "bf_avis_sans_corps")
    def _compute_body_html(self):
        """🔴 Un avis ne garde jamais un corps qu'Odoo ne garde pas. Un module
        qui envoie un `mail.mail` nu en `auto_delete` (`bf_sign`, le sondage
        pulse) le fait pour qu'un jeton ne soit
        conservé nulle part ; sans ce garde, le repli sur `mail.mail.body_html`
        figerait le lien dans l'avis, et le garde « on n'efface jamais un corps
        stocké » le garderait pour toujours."""
        sans = self.filtered("bf_avis_sans_corps")
        super(BfEmailAvis, self - sans)._compute_body_html()
        for rec in sans:
            rec.body_html = ""

    # ------------------------------------------------------------------
    # Un avis ne passe pas par les règles, et une vraie copie le remplace
    # ------------------------------------------------------------------
    def _apply_rules(self, allow_outbound=True, rules=None):
        return super(BfEmailAvis, self.filtered(
            lambda r: r.direction != AVIS))._apply_rules(
                allow_outbound=allow_outbound, rules=rules)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        copies = records.filtered(
            lambda r: r.direction != AVIS and (r.mail_message_id or r.message_id_header))
        if copies:
            # Une vraie copie (chatter, passerelle, IMAP) remplace l'avis du
            # même message pour le même usager. Reconnue par le message, ou par
            # son Message-ID, que l'avis garde comme racine de fil : une copie
            # IMAP n'est pas toujours reliée au `mail.message`.
            entetes = [h for h in copies.mapped("message_id_header") if h]
            domaine = [("mail_message_id", "in", copies.mail_message_id.ids)]
            if entetes:
                domaine = ["|"] + domaine + [("thread_root_id", "in", entetes)]
            avis = self.sudo().with_context(active_test=False).search(
                [("direction", "=", AVIS), ("id", "not in", records.ids)] + domaine)
            par_message = {(r.mail_message_id.id, r.user_id.id) for r in copies if r.mail_message_id}
            par_entete = {(r.message_id_header, r.user_id.id) for r in copies if r.message_id_header}
            doublons = avis.filtered(
                lambda a: (a.mail_message_id.id, a.user_id.id) in par_message
                or (a.thread_root_id, a.user_id.id) in par_entete)
            if doublons:
                doublons.unlink()
        return records

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------
    @api.model
    def _bf_avis_exclus(self):
        """Les comptes qui ne déclenchent rien au sens de la règle du propriétaire (« envoyé
        par Gen, un visiteur ou une tâche planifiée ») : le superutilisateur,
        ceux de ``bf_email.route_exclude_user_ids`` (API des rencontres…), ceux
        de ``bf_email.avis_exclude_user_ids`` (propre aux avis), et l'usager de
        Gen (``bf_gen_mail.owner_gen_email``).

        ⚠️ Gen n'est PAS ajouté à ``route_exclude_user_ids`` : ce paramètre
        commande aussi la projection du chatter dans la boîte de Gen.
        """
        ICP = self.env["ir.config_parameter"].sudo()
        exclus = {1}
        for cle in ("bf_email.route_exclude_user_ids", "bf_email.avis_exclude_user_ids"):
            raw = ICP.get_param(cle, "") or ""
            exclus |= {int(t) for t in raw.replace(";", ",").split(",") if t.strip().isdigit()}
        gen = (ICP.get_param("bf_gen_mail.owner_gen_email", "") or "").strip()
        if gen:
            exclus |= set(self.env["res.users"].sudo().with_context(active_test=False).search(
                [("login", "=ilike", gen)]).ids)
        return exclus

    @api.model
    def _bf_avis_proprietaires(self, message, createur=None):
        """Les usagers à qui l'avis de ``message`` appartient, selon la règle du propriétaire."""
        return self._bf_avis_resolution(message, createur)[0]

    @api.model
    def _bf_avis_resolution(self, message, createur=None):
        """``(usagers, motif)``, motif = ``declencheur``, ``responsable`` ou
        ``repli`` : le corps d'un avis rangé au repli pour un visiteur n'est
        pas gardé."""
        Users = self.env["res.users"].sudo()
        exclus = self._bf_avis_exclus()

        def interne(u):
            return u and u.active and not u.share and u.id not in exclus

        # L'auteur du MESSAGE d'abord : le courriel peut avoir été créé plus
        # tard par la file d'envoi ou une tâche planifiée, sous un autre compte
        # que la personne qui a agi.
        for candidat in (message.create_uid, createur):
            if interne(candidat):
                return candidat, "declencheur"
        if message.model and message.res_id and message.model in self.env:
            try:
                fiche = self.env[message.model].sudo().browse(message.res_id).exists()
                champ = fiche._fields.get("user_id") if fiche else None
                if champ is not None and champ.type == "many2one" and champ.comodel_name == "res.users":
                    if interne(fiche.user_id):
                        return fiche.user_id, "responsable"
            except Exception:
                _logger.debug("bf_email avis : responsable illisible sur %s,%s",
                              message.model, message.res_id, exc_info=True)
        # Repli : pas le groupe « administrateur des courriels », qui donnerait
        # à ses membres la lecture de TOUTES les boîtes, mais un compte nommé,
        # par défaut l'administrateur principal.
        # Réservé à un administrateur système : la capture se fait en
        # superutilisateur, et le repli reçoit des corps de fiches qu'un autre
        # compte ne pourrait pas lire. Un paramètre qui pointe ailleurs est
        # ignoré au profit de l'administrateur principal.
        brut = (self.env["ir.config_parameter"].sudo().get_param(
            "bf_email.avis_repli_user_id", "") or "").strip()
        repli = Users.browse(int(brut)).exists() if brut.isdigit() else Users.browse()
        if not (repli and repli.active and not repli.share
                and repli.has_group("base.group_system")):
            repli = self.env.ref("base.user_admin", raise_if_not_found=False)
        if repli and repli.active and not repli.share:
            return repli, "repli"
        return Users.browse(), "repli"

    @api.model
    def _bf_avis_adresses_internes(self):
        """Toutes les adresses de la maison : celles des usagers internes, de
        leurs comptes et de leurs alias (`_get_self_addresses`). Un avis parti
        vers un alias d'un collègue n'a pas sa place dans un historique de
        contact."""
        # Gardé sur le curseur, donc pour la transaction : Odoo appelle
        # `_postprocess_sent_message` courriel par courriel, et une infolettre
        # de 2 000 courriels relirait sinon 2 000 fois usagers et comptes.
        cache = self.env.cr.cache
        if "bf_email_avis_internes" in cache:
            return cache["bf_email_avis_internes"]
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        adresses = set()
        for u in Users.search([("share", "=", False)]):
            for brute in self._get_self_addresses(u):
                adresses |= set(email_normalize_all(brute) or [brute])
        cache["bf_email_avis_internes"] = adresses
        return adresses

    @api.model
    def _bf_capter_avis(self, mails):
        """Une ligne « avis » par message et par propriétaire, pour les
        ``mail.mail`` qui viennent de partir. Appelé en superutilisateur."""
        par_message = {}
        masse = "mailing_id" in mails._fields
        for mail in mails:
            message = mail.mail_message_id
            if not message or message.message_type not in TYPES_AVIS:
                continue
            # Les infolettres : le module d'envoi de masse garde déjà une
            # trace par destinataire, et une instance en envoie des dizaines
            # de milliers par trimestre.
            if masse and mail.mailing_id:
                continue
            adresses = set(email_normalize_all(mail.email_to or ""))
            adresses |= set(email_normalize_all(mail.email_cc or ""))
            for p in mail.recipient_ids:
                adresses |= set(email_normalize_all(p.email or ""))
            entree = par_message.setdefault(message.id, {"message": message, "a": set(),
                                                         "createur": mail.create_uid,
                                                         "sans_corps": False})
            entree["a"] |= adresses
            # Odoo supprime ce courriel après l'envoi. Son corps rendu ne se
            # garde que si le message, lui, garde un corps : une notification
            # au corps vide (simple suivi sur une fiche) n'a que le rendu du
            # courriel, dont les boutons portent un lien d'accès au portail.
            if mail.auto_delete and (not mail.is_notification or not message.body):
                entree["sans_corps"] = True
        if not par_message:
            return self.browse()
        internes = self._bf_avis_adresses_internes()
        return self._bf_creer_avis(par_message, internes)

    @api.model
    def _bf_creer_avis(self, par_message, internes):
        """``par_message`` : {id: {"message", "a" (adresses), "createur"}}."""
        crees = self.browse()
        discussion = self.env.ref("mail.mt_comment", raise_if_not_found=False)
        for entree in par_message.values():
            message, adresses = entree["message"], entree["a"]
            # Une discussion est un vrai courriel écrit par quelqu'un, même
            # quand le code l'a posté en `notification` (message_post par
            # défaut, mail_notification_custom_subject) : ce n'est pas un avis.
            if discussion and message.subtype_id == discussion:
                continue
            # Les courriels de compte (nouveau mot de passe, invitation) portent
            # un lien à jeton : ils ne se gardent chez personne d'autre.
            if message.model in MODELES_DE_COMPTE:
                continue
            # Sans fiche et déclenché par un visiteur (compte public ou portail
            # qui demande un code à usage unique) : rien à rattacher, et le
            # corps porte souvent un secret. Une tâche planifiée sans fiche
            # (état de banque d'heures, rapport) passe, et va au repli.
            createur = message.create_uid
            if not message.model and (not createur or createur.share):
                continue
            externes = sorted(adresses - internes)
            # Un avis qui ne touche que des collègues n'a rien à faire dans
            # l'historique d'un contact.
            if not externes:
                continue
            proprietaires, motif = self._bf_avis_resolution(message, entree.get("createur"))
            # Déclenché par un visiteur et rangé au repli (dépôt d'un transfert,
            # code d'un sondage de rendez-vous) : l'enveloppe seulement. L'avis
            # dit qu'un courriel est parti, sans garder un code qui ne
            # regarde que le visiteur.
            visiteur = bool(message.create_uid and message.create_uid.share)
            for proprietaire in proprietaires:
                cles = [("mail_message_id", "=", message.id)]
                if message.message_id:
                    cles = ["|"] + cles + [("message_id_header", "=", message.message_id)]
                deja = self.sudo().with_context(active_test=False).search(
                    [("user_id", "=", proprietaire.id)] + cles)
                if deja:
                    # Odoo découpe une notification en plusieurs courriels (par
                    # langue, par paquet de 50) : les destinataires du courriel
                    # suivant rejoignent l'avis déjà créé.
                    avis = deja.filtered(lambda r: r.direction == AVIS
                                         and r.mail_message_id == message)
                    if avis and avis == deja:
                        # Un courriel suivant qu'Odoo supprime suffit à faire de
                        # l'avis une enveloppe : Odoo appelle la capture un
                        # courriel à la fois.
                        if (entree.get("sans_corps") or (visiteur and motif == "repli")) \
                                and not avis[0].bf_avis_sans_corps:
                            avis[0].write({"bf_avis_sans_corps": True})
                        connues = set(email_normalize_all(avis[0].email_to or ""))
                        neuves = [a for a in externes if a not in connues]
                        if neuves:
                            avis[0].write({"email_to": ", ".join(sorted(connues | set(neuves)))})
                    continue
                vals = self._bf_valeurs_avis(message, externes, proprietaire)
                vals["bf_avis_sans_corps"] = bool(entree.get("sans_corps")) or (
                    visiteur and motif == "repli")
                crees |= self.sudo().with_context(bf_email_avis=True).create(vals)
        return crees

    @api.model
    def _bf_valeurs_avis(self, message, externes, proprietaire):
        nom_fiche = ""
        if message.model and message.res_id and message.model in self.env:
            try:
                nom_fiche = self.env[message.model].sudo().browse(
                    message.res_id).exists().display_name or ""
            except Exception:
                nom_fiche = ""
        # Le contact principal : la première fiche externe qui porte une des
        # adresses. Le reste passe par la table des adresses.
        Partner = self.env["res.partner"].sudo()
        contact = Partner.browse()
        for adresse in externes:
            contact = Partner.search([("email_normalized", "=", adresse)], limit=1)
            if contact:
                break
        return {
            "date": message.date,
            "subject": message.subject or message.record_name or nom_fiche or "",
            "email_from": message.email_from or "",
            "email_to": ", ".join(externes),
            "direction": AVIS,
            "source": "chatter",
            "status": "read",
            "is_handled": True,
            "user_id": proprietaire.id,
            "mail_message_id": message.id,
            # ⚠️ Pas de `message_id_header` : la contrainte (Message-ID,
            # société, usager) réserverait la place de la vraie copie IMAP du
            # même courriel, relevée plus tard dans la boîte d'un destinataire
            # interne, qui serait alors refusée. Le Message-ID reste lisible
            # comme racine de fil, et sert à reconnaître cette copie.
            "message_id_header": False,
            "thread_root_id": message.message_id or False,
            "res_model": message.model or False,
            "res_id": message.res_id or False,
            "record_name": nom_fiche[:200],
            "partner_id": contact.id or False,
            "author_id": message.author_id.id if message.author_id.exists() else False,
            "category": "notification",
        }


class BfEmailAvisMobile(models.Model):
    _inherit = "bf.email"

    @api.model
    def _inbox_folder_defs(self):
        """Un avis naît traité, mais « Traités » est la liste de ce qu'on a
        traité soi-même : il n'y paraît pas. Il reste dans « Tous les
        courriels », sous le filtre de contact et dans les catégories."""
        defs = super()._inbox_folder_defs()
        for d in defs:
            if d.get("key") == "handled" and d.get("domain") is not None:
                d["domain"] = list(d["domain"]) + [("direction", "!=", AVIS)]
        return defs

    @api.model
    def _thread_domain(self, thread_key):
        """Les fils du téléphone (`get_mobile_conversation`) : un client qui
        répond à un envoi fait par gabarit prend le Message-ID de l'avis pour
        racine, et l'appli afficherait l'avis comme reçu de notre adresse."""
        return super()._thread_domain(thread_key) + [("direction", "!=", AVIS)]

    @api.model
    def _mobile_filter_sql(self, name):
        """Le téléphone ne reçoit pas les avis : son appli ne connaît que les
        directions « in » et « out », et il n'a pas d'historique de contact
        où ils auraient un sens. Concaténé, jamais reformaté : la clause
        d'origine porte ses propres ``%s``."""
        sql, params = super()._mobile_filter_sql(name)
        return "(" + sql + ") AND direction <> 'notice'", params


class MailMailAvis(models.Model):
    _inherit = "mail.mail"

    def _postprocess_sent_message(self, success_pids, failure_reason=False, failure_type=None):
        # Avant `super()` : il supprime les courriels `auto_delete`, et leurs
        # destinataires avec eux. Un échec de capture ne doit JAMAIS empêcher
        # l'envoi de se clore : point de sauvegarde, journal, et on continue.
        # Une adresse invalide ou manquante n'empêche pas le courriel de partir
        # vers les autres destinataires : Odoo le marque envoyé et le supprime.
        if failure_type in (None, False, "mail_email_invalid", "mail_email_missing"):
            try:
                with self.env.cr.savepoint():
                    self.env["bf.email"].sudo()._bf_capter_avis(
                        self.sudo().filtered(lambda m: m.state == "sent"))
            except Exception:
                _logger.warning("bf_email : capture d'un avis automatique impossible",
                                exc_info=True)
        return super()._postprocess_sent_message(
            success_pids, failure_reason=failure_reason, failure_type=failure_type)
