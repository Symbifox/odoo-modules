"""Backend origination of an OTP-gated secure transfer (message and/or files).

This is the "hold the mail until identity is proven" send: an internal Odoo
user composes a message — and now attaches files — to one or more contacts;
instead of the content going out in clear, a link-only notification is sent and
everything is revealed on the branded page ONLY after the recipient enters a
one-time code (delivered by e-mail or SMS). It reuses the whole secure.transfer
machinery (branding, Loi 25 access journal, expiry, purge) — it just forces the
recipient-OTP gate on and picks the delivery channel.

⚠ Files here do NOT ride the public flow. On the public page the browser PUTs
straight to S3 and no byte ever enters Odoo; a backend composer cannot do that,
so the bytes necessarily pass through the worker's memory
(``secure.transfer._backend_add_file`` → ``s3.put_bytes``). That is why this
path is capped (``bf_securetransfer.backend_max_upload_mb``, hard-limited in the
model) and why anything large belongs on /secrets instead.

⚠ This wizard creates its transfer in ``sudo()``. Every guard the public flow
gets from ``api_create``/``action_finalize`` therefore has to be re-applied HERE
by hand — sender allowlist, recipient allowlist, daily sender quota, password
availability, retention grid. They were all missing while this was a
message-only composer, which made the backend a quiet way around a client
brand's anti-piggyback rules. Do not remove them.
"""
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..models import s3
from ..models import sms
from ..models.secure_transfer import MAX_SUBJECT_LEN

_logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 10


class SecureSendWizard(models.TransientModel):
    _name = "secure.transfer.send.wizard"
    _description = "Envoyer un message ou des fichiers sécurisés (code destinataire)"

    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Marque / domaine",
        required=True,
        domain="[('active', '=', True), ('fixed_recipient', '=', False)]",
        help="Habillage et domaine du lien envoyé au destinataire.",
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Destinataires",
        help="Contacts Odoo. Pour le canal SMS, leur mobile (ou téléphone) sert "
             "à l'envoi du code ; sans numéro, le courriel prend le relais.",
    )
    extra_emails = fields.Char(
        string="Autres courriels",
        help="Adresses hors carnet, séparées par des virgules. Elles reçoivent "
             "toujours leur code par courriel (aucun numéro connu).",
    )
    subject = fields.Char(
        string="Objet",
        help="Court intitulé visible dans l'objet du courriel : c'est tout ce "
             "que le destinataire saura avant de saisir son code. Il voyage en "
             "clair — rien de confidentiel n'y a sa place.",
    )
    # No longer required: a transfer may now carry files with no covering note.
    # « au moins l'un des deux » is enforced in action_send.
    message = fields.Text(
        string="Message sécurisé",
        help="Contenu retenu jusqu'à la validation du code. Texte brut, rendu "
             "échappé — jamais interprété comme HTML.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "st_send_wizard_attachment_rel",
        "wizard_id",
        "attachment_id",
        string="Fichiers",
        help="Fichiers joints au transfert. Ils sont déposés sur le stockage "
             "chiffré puis RETIRÉS d'Odoo : seul le lien sécurisé y donne "
             "accès, et le destinataire doit saisir son code.",
    )
    otp_channel = fields.Selection(
        selection=[("email", "Courriel"), ("sms", "SMS")],
        string="Canal du code", default="email", required=True,
    )
    # -- Audience ouverte ------------------------------------------------------
    # ⚠ C'est le SEUL chemin de création d'une audience ouverte. Le formulaire
    # public reste en destinataires nommés : y ouvrir le mode donnerait à un
    # visiteur anonyme le pouvoir de fabriquer un lien qui envoie des codes
    # signés DKIM — et des SMS facturés — à des adresses de son choix. Ici,
    # l'auteur est un utilisateur interne authentifié, tracé, et soumis au
    # quota quotidien de son adresse.
    audience_mode = fields.Selection(
        selection=[
            ("declared", "Destinataires nommés"),
            ("open", "Audience ouverte (le visiteur se déclare)"),
        ],
        string="Mode de transmission", default="declared", required=True,
        help="« Audience ouverte » : le lien ne vise personne. Il vous est "
             "renvoyé à vous, vous le diffusez, et chaque personne qui l'ouvre "
             "s'identifie puis confirme par un code à usage unique.",
    )
    brand_allows_audience = fields.Boolean(
        compute="_compute_brand_audience", string="Audience offerte")
    brand_allows_audience_sms = fields.Boolean(
        compute="_compute_brand_audience", string="SMS d'audience offert")
    audience_domains = fields.Text(
        string="Domaines admis",
        help="Vide = la liste de la marque. Une entrée par ligne : "
             "@client.com, ou une adresse complète.",
    )
    audience_max = fields.Integer(
        string="Visiteurs max.", default=0,
        help="0 = valeur de la marque. Ce plafond est ce qui empêche un lien "
             "diffusé trop largement de servir de relais de courriel.",
    )
    audience_max_downloads = fields.Integer(
        string="Téléchargements par visiteur", default=0,
        help="0 = illimité. Compté séparément pour chaque personne.",
    )
    audience_allow_sms = fields.Boolean(
        string="Offrir le code par SMS",
        help="Laisse un visiteur s'identifier par son mobile plutôt que par "
             "courriel. Chaque SMS a un coût réel.",
    )
    notify_on_join = fields.Boolean(
        string="M'aviser à chaque nouveau visiteur", default=True,
    )
    retention_days = fields.Integer(string="Disponible (jours)", default=7)
    password = fields.Char(
        string="Mot de passe (optionnel)",
        help="Couche supplémentaire, en plus du code. À communiquer par un "
             "autre canal — il ne figure dans aucun courriel.",
    )
    sender_name = fields.Char(
        string="Nom de l'expéditeur",
        default=lambda self: self.env.user.name,
    )
    sender_email = fields.Char(
        string="Courriel de l'expéditeur", required=True,
        default=lambda self: self.env.user.email,
        help="Reçoit l'accusé d'envoi. Seul un gestionnaire du transfert "
             "sécurisé peut envoyer sous une autre identité que la sienne.",
    )
    sms_configured = fields.Boolean(compute="_compute_sms_configured")
    sms_hint = fields.Char(compute="_compute_sms_configured")
    # Identity pinning: a plain user sends as themselves, full stop. The field
    # is made readonly in the form for them, and action_send re-forces the
    # value server-side — the view is a courtesy, the model is the guard.
    sender_locked = fields.Boolean(
        string="Identité verrouillée", compute="_compute_sender_locked")
    upload_hint = fields.Char(
        string="Limite de téléversement", compute="_compute_upload_hint")
    # Told BEFORE the send rather than as a refusal after it: the brand may
    # forbid this sender, and finding out only once the files are picked is a
    # bad way to learn it.
    brand_warning = fields.Char(
        string="Avertissement de marque", compute="_compute_brand_warning")

    @api.depends_context("uid")
    def _compute_sms_configured(self):
        ok = sms.configured(self.env)
        hint = (
            _("Canal SMS prêt (VoIP.ms configuré).") if ok else
            _("Le canal SMS n'est pas configuré sur cette instance — voir "
              "Configuration › Paramètres. Le courriel reste disponible.")
        )
        for rec in self:
            rec.sms_configured = ok
            rec.sms_hint = hint

    @api.depends("brand_id")
    def _compute_brand_audience(self):
        """Ce que la marque choisie offre. Sert la visibilité du formulaire ;
        `action_send` revérifie, et la contrainte du modèle est la ceinture."""
        sms_ok = sms.configured(self.env)
        for rec in self:
            brand = rec.brand_id
            rec.brand_allows_audience = bool(brand.allow_open_audience)
            rec.brand_allows_audience_sms = bool(
                brand.allow_open_audience and brand.allow_audience_sms and sms_ok)

    @api.onchange("brand_id")
    def _onchange_brand_audience(self):
        """Une marque qui n'offre pas l'audience ouverte ramène le mode à
        « destinataires nommés » — sinon le formulaire garderait un mode que
        l'envoi refusera, et l'utilisateur ne le découvrirait qu'au bouton."""
        for rec in self:
            if rec.audience_mode == "open" and not rec.brand_id.allow_open_audience:
                rec.audience_mode = "declared"
            if rec.audience_allow_sms and not rec.brand_allows_audience_sms:
                rec.audience_allow_sms = False

    @api.depends_context("uid")
    def _compute_sender_locked(self):
        locked = not self.env.user.has_group(
            "bf_securetransfer.group_securetransfer_manager")
        for rec in self:
            rec.sender_locked = locked

    @api.depends_context("uid")
    def _compute_upload_hint(self):
        mb = self.env["secure.transfer"]._backend_max_upload_bytes() // (1024 * 1024)
        hint = _(
            "Jusqu'à %s Mo au total depuis cet écran. Au-delà, passez par la "
            "page publique d'envoi : elle téléverse directement vers le "
            "stockage, sans limite pratique.", mb)
        for rec in self:
            rec.upload_hint = hint

    @api.depends("brand_id", "sender_email")
    def _compute_brand_warning(self):
        for rec in self:
            warning = ""
            email = email_normalize(rec.sender_email or "") or ""
            if rec.brand_id and email and not rec.brand_id._sender_allowed(email):
                warning = _(
                    "« %(brand)s » n'accepte pas d'envoi depuis %(email)s "
                    "(liste d'expéditeurs autorisés). Choisissez une autre "
                    "marque.",
                    brand=rec.brand_id.display_name, email=email)
            rec.brand_warning = warning

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Default brand: the instance default, else the first eligible brand.
        Brand = self.env["secure.transfer.brand"].sudo()
        brand = Brand.search(
            [("active", "=", True), ("is_default", "=", True),
             ("fixed_recipient", "=", False)], limit=1)
        if not brand:
            brand = Brand.search(
                [("active", "=", True), ("fixed_recipient", "=", False)], limit=1)
        if brand:
            res.setdefault("brand_id", brand.id)
        # Entrée de menu dédiée à l'audience ouverte.
        # ⚠ Poser `default_audience_mode` dans le contexte de l'action ne
        # suffit PAS : `_onchange_brand_audience` s'exécute au chargement du
        # formulaire, n'y trouve encore aucune marque, et une marque vide
        # n'offre rien — le mode retomberait à « destinataires nommés » sans
        # que personne le voie. C'est le fait de choisir ici une marque qui
        # offre RÉELLEMENT le mode qui fait survivre le défaut à l'onchange.
        # Recherche sans sudo, contrairement au repli ci-dessus : la règle de
        # société doit s'appliquer, et proposer d'emblée la marque d'une autre
        # société serait une fuite avant même le premier envoi.
        if self.env.context.get("st_open_audience"):
            Offering = self.env["secure.transfer.brand"]
            already = Offering.browse(res.get("brand_id") or 0).exists()
            # Une marque explicitement demandée qui offre déjà le mode est
            # gardée : l'entrée de menu répare un défaut, elle ne dicte pas
            # un choix que l'appelant a posé.
            offering = already if already.allow_open_audience else Offering.search(
                [("active", "=", True), ("fixed_recipient", "=", False),
                 ("allow_open_audience", "=", True)], limit=1)
            if not offering:
                # Un formulaire muet en « destinataires nommés » ferait croire
                # que le mode n'existe pas. Dire où l'ouvrir vaut mieux.
                raise UserError(_(
                    "Aucune marque n'offre l'audience ouverte pour le moment.\n\n"
                    "Ouvrez-la sur la marque visée — Configuration › Marques › "
                    "« Audience ouverte offerte » — et fixez-y un nombre maximal "
                    "de visiteurs. Le lien se créera ensuite depuis ce menu."))
            res["brand_id"] = offering.id
            res["audience_mode"] = "open"
        # Prefill recipients when launched from a res.partner list/form.
        if self.env.context.get("active_model") == "res.partner":
            ids = self.env.context.get("active_ids") \
                or ([self.env.context["active_id"]]
                    if self.env.context.get("active_id") else [])
            if ids:
                res.setdefault("partner_ids", [(6, 0, ids)])
        return res

    def _resolve_recipients(self):
        """Return (recipient_emails_list, sms_map dict) from the picked contacts
        and the extra-email field, normalized and deduped."""
        self.ensure_one()
        emails, sms_map = [], {}
        for partner in self.partner_ids:
            norm = email_normalize(partner.email or "")
            if not norm:
                raise UserError(_(
                    "Le contact « %s » n'a pas d'adresse courriel valide.",
                    partner.display_name))
            if norm not in emails:
                emails.append(norm)
            phone = sms.normalize_na(partner.mobile or partner.phone or "")
            if phone:
                sms_map[norm] = phone
        for raw in (self.extra_emails or "").replace(";", ",").split(","):
            raw = raw.strip()
            if not raw:
                continue
            norm = email_normalize(raw)
            if not norm:
                raise UserError(_("Adresse courriel invalide : %s", raw))
            if norm not in emails:
                emails.append(norm)
        if not emails:
            raise UserError(_("Ajoutez au moins un destinataire."))
        if len(emails) > MAX_RECIPIENTS:
            raise UserError(_("Maximum %s destinataires.", MAX_RECIPIENTS))
        return emails, sms_map

    def _effective_sender(self):
        """(name, email) this transfer may actually claim to be from.

        A securetransfer MANAGER may compose on someone else's behalf (a shared
        « info@ » identity, a send relayed for a colleague). Anyone else sends
        as themselves: the form field is only readonly, and a TransientModel is
        writable over RPC, so the pinning has to happen here.
        """
        self.ensure_one()
        user = self.env.user
        if not user.has_group("bf_securetransfer.group_securetransfer_manager"):
            own = email_normalize(user.email or "") or ""
            if not own:
                raise UserError(_(
                    "Votre compte n'a pas d'adresse courriel : impossible "
                    "d'envoyer un transfert sécurisé en votre nom. Demandez à "
                    "un administrateur de renseigner votre adresse."))
            return (user.name or "").strip(), own
        email = email_normalize(self.sender_email or "") \
            or (self.sender_email or "").strip()
        if not email:
            raise UserError(_("Une adresse courriel d'expéditeur est requise."))
        return (self.sender_name or "").strip(), email

    def _collect_attachments(self):
        """[(filename, bytes)] for the picked attachments, size-checked.

        Read up front, BEFORE the transfer exists: an oversize batch must be
        refused without leaving a draft, an S3 object or a journal entry behind.
        """
        self.ensure_one()
        if not self.attachment_ids:
            return []
        ceiling = self.env["secure.transfer"]._backend_max_upload_bytes()
        total = sum(att.file_size or 0 for att in self.attachment_ids)
        if total > ceiling:
            raise UserError(_(
                "Les fichiers joints totalisent %(total).1f Mo ; cet écran en "
                "accepte %(max)s au maximum. Pour un envoi plus gros, utilisez "
                "la page publique d'envoi (téléversement direct vers le "
                "stockage).",
                total=total / (1024 * 1024), max=ceiling // (1024 * 1024)))
        out = []
        for att in self.attachment_ids:
            data = att.raw or b""
            if not data:
                raise UserError(_(
                    "Le fichier « %s » est vide ou illisible.",
                    att.name or "?"))
            # The stored file_size is metadata; the bytes are the truth. A
            # mismatch means the attachment was tampered with between upload
            # and send, so re-check the ceiling against what we actually hold.
            out.append((att.name or "fichier", data))
        if sum(len(d) for _n, d in out) > ceiling:
            raise UserError(_(
                "Les fichiers joints dépassent la limite de %s Mo de cet écran.",
                ceiling // (1024 * 1024)))
        return out

    def _transfer_vals(self, vals):
        """Point d'extension : les valeurs du transfert, juste avant sa
        création. Le socle rend le dictionnaire tel quel.

        Existe pour qu'un module qui ajoute un réglage d'envoi (le pont
        ``bf_securetransfer_sign`` et son entente de confidentialité) n'ait pas
        à réécrire `action_send` en entier — donc à recopier, et un jour à
        laisser vieillir, les huit contrôles anti-rebond qu'il porte."""
        self.ensure_one()
        return vals

    def action_send(self):
        self.ensure_one()
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_user"):
            raise UserError(_("Action réservée aux utilisateurs du transfert sécurisé."))
        open_audience = self.audience_mode == "open"
        if self.otp_channel == "sms" and not open_audience \
                and not sms.configured(self.env):
            raise UserError(_(
                "Le canal SMS n'est pas configuré (VoIP.ms). Configurez-le dans "
                "Configuration › Paramètres, ou choisissez le canal Courriel."))
        if open_audience and self.audience_allow_sms and not sms.configured(self.env):
            raise UserError(_(
                "Le canal SMS n'est pas configuré (VoIP.ms) : les visiteurs ne "
                "pourraient pas recevoir de code sur leur mobile."))

        brand = self.brand_id
        # A drop page forces every send to its own owner. It is excluded from
        # the field domain, but a domain is a UI hint — re-check it here or an
        # RPC caller redirects the transfer to that person instead.
        if brand.fixed_recipient:
            raise UserError(_(
                "« %s » est une page de dépôt : elle ne peut servir qu'à "
                "recevoir, jamais à envoyer.", brand.display_name))

        sender_name, sender_email = self._effective_sender()
        # -- anti-piggyback, sender side (public flow: api_create + finalize)
        if not brand._sender_allowed(sender_email):
            raise UserError(_(
                "« %(brand)s » n'autorise pas les envois depuis %(email)s.",
                brand=brand.display_name, email=sender_email))

        if open_audience:
            # La marque est le seul endroit où l'opérateur a consenti au mode.
            # La contrainte du modèle dirait la même chose, mais un message
            # d'assistant vaut mieux qu'une erreur de validation.
            if not brand.allow_open_audience:
                raise UserError(_(
                    "« %s » n'offre pas l'audience ouverte. Activez-la sur la "
                    "marque, ou choisissez « Destinataires nommés ».",
                    brand.display_name))
            if self.audience_allow_sms and not brand.allow_audience_sms:
                raise UserError(_(
                    "« %s » n'offre pas le code par SMS en audience ouverte.",
                    brand.display_name))
            # ⚠ Un plafond à 0 (illimité) sur un lien que personne ne nomme,
            # c'est un relais de courriel ouvert. On refuse plutôt que de
            # laisser l'opérateur le découvrir dans sa facture VoIP.ms ou dans
            # une plainte anti-pourriel.
            if not (self.audience_max or brand.audience_max_default):
                raise UserError(_(
                    "Fixez un nombre maximal de visiteurs (ou renseignez-le "
                    "sur la marque) : sans plafond, ce lien peut faire partir "
                    "un code vers autant d'adresses que quelqu'un le souhaite."))
            # Pas de destinataires nommés : le lien revient à l'expéditeur, qui
            # le diffuse. C'est le mode « lien seul » déjà connu du modèle.
            emails, sms_map = [], {}
        else:
            emails, sms_map = self._resolve_recipients()
            # -- anti-piggyback, destination side (public flow: api_create)
            bad = [r for r in emails if not brand._recipient_allowed(r)]
            if bad:
                raise UserError(_(
                    "« %(brand)s » n'autorise l'envoi qu'à certaines adresses. "
                    "Destinataire(s) non autorisé(s) : %(bad)s",
                    brand=brand.display_name, bad=", ".join(bad)))

        files = self._collect_attachments()
        if not files and not (self.message or "").strip():
            raise UserError(_("Ajoutez au moins un fichier ou un message."))

        limits = brand._effective_limits()
        retention = self.retention_days or 7
        if retention not in limits["expiry_choices"]:
            raise UserError(_(
                "Durée de mise à disposition non offerte par « %(brand)s » : "
                "%(asked)s jour(s) demandé(s), alors que cette marque offre "
                "%(choices)s.",
                brand=brand.display_name, asked=retention,
                choices=", ".join(str(c) for c in limits["expiry_choices"])))
        if self.password and not brand.allow_password:
            raise UserError(_(
                "Le mot de passe supplémentaire n'est pas offert pour "
                "« %s ».", brand.display_name))

        Transfer = self.env["secure.transfer"].sudo()
        # -- daily per-sender quota (public flow: finalize). The backend is not
        #    a way around the anti-abuse counters.
        Transfer._check_sender_quota(sender_email)

        transfer = Transfer.create(self._transfer_vals({
            "brand_id": brand.id,
            "sender_name": sender_name,
            "sender_email": sender_email,
            "recipient_emails": ", ".join(emails),
            "subject": Transfer._clean_line(self.subject, MAX_SUBJECT_LEN),
            "message": (self.message or "").strip(),
            "retention_days": retention,
            "force_recipient_otp": True,
            "recipient_otp_channel": self.otp_channel,
            "recipient_sms_map": json.dumps(sms_map) if sms_map else False,
            "audience_mode": self.audience_mode,
            "audience_domains": (self.audience_domains or "").strip()
            if open_audience else False,
            "audience_max": self.audience_max if open_audience else 0,
            "audience_max_downloads": self.audience_max_downloads
            if open_audience else 0,
            "audience_allow_sms": bool(self.audience_allow_sms and open_audience),
            "notify_on_join": bool(self.notify_on_join) if open_audience else False,
            # Inherit the brand's download-notification policy, like api_create
            # does. Left out, a backend send silently opted out of the notice
            # the same brand gives every public sender.
            "notify_on_download": brand.notify_on_download,
            "locale": self.env.user.lang if self.env.user.lang in (
                "fr_CA", "en_CA") else "fr_CA",
            "ip_created": "backend",
            "ua_created": "backend:%s" % self.env.user.login,
            "state": "draft",
        }))

        uploaded_keys = []
        try:
            for filename, data in files:
                st_file = transfer._backend_add_file(filename, data)
                uploaded_keys.append(st_file.s3_key)
        except Exception:
            # The DB rolls back with the exception, so the file rows and the
            # transfer vanish — but the S3 objects would not. The hourly orphan
            # sweep would catch them within its 48 h grace; cleaning up now
            # keeps a refused send from paying for storage until then.
            if uploaded_keys:
                try:
                    s3.delete_keys(self.env, uploaded_keys)
                except Exception:  # noqa: BLE001 — best effort on the way out
                    _logger.exception(
                        "bf_securetransfer: nettoyage S3 après échec d'envoi "
                        "backend impossible (%s)", ", ".join(uploaded_keys))
            raise

        if self.password:
            transfer._set_password(self.password)
        if open_audience:
            transfer._log(
                "created", actor=self.env.user.login,
                note=_("Transfert en audience ouverte créé depuis le backend "
                       "par %(login)s (%(files)s fichier(s), %(max)s visiteur(s) "
                       "au maximum, SMS %(sms)s)")
                % {
                    "login": self.env.user.login,
                    "files": len(files),
                    "max": self.audience_max or brand.audience_max_default,
                    "sms": _("offert") if self.audience_allow_sms else _("non offert"),
                })
        else:
            transfer._log(
                "created", actor=self.env.user.login,
                note=_("Transfert sécurisé créé depuis le backend par %(login)s "
                       "(canal %(channel)s, %(recipients)s destinataire(s), "
                       "%(files)s fichier(s))")
                % {
                    "login": self.env.user.login,
                    "channel": _("SMS") if self.otp_channel == "sms" else _("courriel"),
                    "recipients": len(emails),
                    "files": len(files),
                })
        # Activate: rotates the token, arms expiry, sends the link-only
        # notification (secure-message template) + the sender receipt.
        transfer._activate()

        # The operator's copy has served its purpose. Leaving it behind would
        # keep the confidential bytes in Odoo's filestore — inside the nightly
        # backups — which is the exact thing this module exists to avoid.
        self._drop_attachments()

        return {
            "type": "ir.actions.act_window",
            "name": _("Transfert sécurisé"),
            "res_model": "secure.transfer",
            "res_id": transfer.id,
            "view_mode": "form",
            "target": "current",
        }

    def _drop_attachments(self):
        """Remove the operator's uploaded copies from Odoo."""
        self.ensure_one()
        attachments = self.attachment_ids
        if not attachments:
            return
        self.attachment_ids = [(5, 0, 0)]
        attachments.sudo().unlink()

    # ------------------------------------------------------------------ GC
    # Composer copies that never became a transfer. The widget commits each
    # upload in its OWN request, so a cancelled wizard — or one that failed at
    # send, since the failure rolls its unlink back too — leaves the file in the
    # filestore, attached to a transient record the vacuum will drop without
    # touching the attachment. Two hours is well past any live composing
    # session, and this is confidential material: it does not get to linger.
    _ATTACHMENT_GC_HOURS = 2

    @api.model
    def _gc_orphan_attachments(self):
        """Cron: unlink composer attachments older than the grace window.

        Returns the number removed (read by the tests and the server log).
        """
        cutoff = fields.Datetime.now() - timedelta(hours=self._ATTACHMENT_GC_HOURS)
        orphans = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", self._name),
            ("create_date", "<", cutoff),
        ])
        count = len(orphans)
        if count:
            orphans.unlink()
            _logger.info(
                "bf_securetransfer: %s pièce(s) jointe(s) de composition "
                "abandonnée(s) supprimée(s) du filestore", count)
        return count
