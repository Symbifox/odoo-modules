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
    _description = "Send a secure message or files (recipient code)"

    brand_id = fields.Many2one(
        "secure.transfer.brand",
        string="Brand / domain",
        required=True,
        domain="[('active', '=', True), ('fixed_recipient', '=', False)]",
        help="Styling and domain of the link sent to the recipient.",
    )
    partner_ids = fields.Many2many(
        "res.partner",
        string="Recipients",
        help="Odoo contacts. For the SMS channel, their mobile (or phone) "
             "is used to send the code; without a number, email takes "
             "over.",
    )
    extra_emails = fields.Char(
        string="Other emails",
        help="Addresses outside the address book, comma-separated. They "
             "always receive their code by email (no known number).",
    )
    subject = fields.Char(
        string="Subject",
        help="A short line shown in the email subject: it is all the "
             "recipient will know before entering their code. It travels "
             "in the clear, so nothing confidential belongs in it.",
    )
    # No longer required: a transfer may now carry files with no covering note.
    # « au moins l'un des deux » is enforced in action_send.
    message = fields.Text(
        string="Secure message",
        help="Content withheld until the code is validated. Plain text, "
             "rendered escaped, never interpreted as HTML.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "st_send_wizard_attachment_rel",
        "wizard_id",
        "attachment_id",
        string="Files",
        help="Files attached to the transfer. They are uploaded to "
             "encrypted storage and then REMOVED from Odoo: only the "
             "secure link gives access, and the recipient must enter "
             "their code.",
    )
    otp_channel = fields.Selection(
        selection=[("email", "Email"), ("sms", "SMS")],
        string="Code channel", default="email", required=True,
    )
    # -- Préréglage ------------------------------------------------------------
    # Posé AVANT le bloc d'audience : dans le formulaire comme ici, on choisit
    # le préréglage d'abord, et il remplit ce qui suit.
    template_id = fields.Many2one(
        "secure.transfer.template",
        string="Preset",
        help="Replays a data room configuration that is already set up. "
             "The values can still be changed after it is applied.",
    )
    template_note = fields.Text(
        related="template_id.note", string="What this preset is for",
        readonly=True)

    # -- Audience ouverte ------------------------------------------------------
    # ⚠ C'est le SEUL chemin de création d'une audience ouverte. Le formulaire
    # public reste en destinataires nommés : y ouvrir le mode donnerait à un
    # visiteur anonyme le pouvoir de fabriquer un lien qui envoie des codes
    # signés DKIM — et des SMS facturés — à des adresses de son choix. Ici,
    # l'auteur est un utilisateur interne authentifié, tracé, et soumis au
    # quota quotidien de son adresse.
    audience_mode = fields.Selection(
        selection=[
            ("declared", "Named recipients"),
            ("open", "Open audience (the visitor identifies themselves)"),
        ],
        string="Delivery mode", default="declared", required=True,
        help="\"Open audience\": the link targets no one. It is sent back "
             "to you, you share it, and each person who opens it "
             "identifies themselves, then confirms with a one-time code.",
    )
    brand_allows_audience = fields.Boolean(
        compute="_compute_brand_audience", string="Audience offered by "
                                                  "the brand")
    brand_allows_audience_sms = fields.Boolean(
        compute="_compute_brand_audience", string="Audience SMS offered")
    audience_domains = fields.Text(
        string="Allowed domains",
        help="Empty = the brand's list. One entry per line: @client.com, "
             "or a full address.",
    )
    audience_max = fields.Integer(
        string="Max. visitors", default=0,
        help="0 = brand value. This cap is what keeps a widely shared "
             "link from becoming an email relay.",
    )
    audience_max_downloads = fields.Integer(
        string="Downloads per visitor", default=0,
        help="0 = unlimited. Counted separately for each person.",
    )
    audience_allow_sms = fields.Boolean(
        string="Offer the code by SMS",
        help="Lets a visitor identify themselves by mobile rather than by "
             "email. Each SMS has a real cost.",
    )
    notify_on_join = fields.Boolean(
        string="Notify me of each new visitor", default=True,
    )
    retention_days = fields.Integer(string="Available (days)", default=7)
    password = fields.Char(
        string="Password (optional)",
        help="Extra layer, on top of the code. Share it through another "
             "channel: it never appears in an email.",
    )
    sender_name = fields.Char(
        string="Sender name",
        default=lambda self: self.env.user.name,
    )
    sender_email = fields.Char(
        string="Sender email", required=True,
        default=lambda self: self.env.user.email,
        help="Receives the send receipt. Only a secure transfer manager "
             "may send under an identity other than their own.",
    )
    sms_configured = fields.Boolean(compute="_compute_sms_configured")
    sms_hint = fields.Char(compute="_compute_sms_configured")
    # Identity pinning: a plain user sends as themselves, full stop. The field
    # is made readonly in the form for them, and action_send re-forces the
    # value server-side — the view is a courtesy, the model is the guard.
    sender_locked = fields.Boolean(
        string="Identity locked", compute="_compute_sender_locked")
    upload_hint = fields.Char(
        string="Upload limit", compute="_compute_upload_hint")
    # Told BEFORE the send rather than as a refusal after it: the brand may
    # forbid this sender, and finding out only once the files are picked is a
    # bad way to learn it.
    brand_warning = fields.Char(
        string="Brand warning", compute="_compute_brand_warning")

    @api.depends_context("uid")
    def _compute_sms_configured(self):
        ok = sms.configured(self.env)
        hint = (
            _("SMS channel ready (VoIP.ms configured).") if ok else
            _("The SMS channel is not configured on this instance: see "
              "Configuration › Settings. Email remains available.")
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

    @api.onchange("template_id")
    def _onchange_template_id(self):
        """Appliquer le préréglage, et DIRE ce qui n'a pas pu l'être.

        ⚠ La marque est posée en premier : les champs calculés qui en
        dépendent (`brand_allows_audience*`) doivent être à jour avant qu'on
        juge si le mode demandé tient debout.

        ⚠ On ne se fie pas au fait que `_onchange_brand_audience` se rejoue
        derrière nous — l'ordre des onchanges en cascade n'est pas un contrat.
        La coercition est refaite ici, explicitement, et surtout elle est
        RENDUE VISIBLE : un mode qui retombe en silence sur « destinataires
        nommés » est exactement la panne que ce préréglage existe pour
        éviter."""
        warning = None
        for rec in self:
            tmpl = rec.template_id
            if not tmpl:
                continue
            vals = tmpl._apply_vals()
            brand_id = vals.pop("brand_id", None)
            if brand_id:
                rec.brand_id = brand_id
            for fname, value in vals.items():
                rec[fname] = value
            brand = rec.brand_id
            if rec.audience_mode == "open" and not brand.allow_open_audience:
                rec.audience_mode = "declared"
                rec.audience_allow_sms = False
                warning = {
                    "title": _("Preset partly applied"),
                    "message": _(
                        "\"%(tmpl)s\" asks for an open audience, but the "
                        "\"%(brand)s\" brand does not offer it. The mode "
                        "went back to \"named recipients\".\n\nOpen the "
                        "audience on this brand (Configuration › Brands), "
                        "or choose a brand that offers it.",
                        tmpl=tmpl.display_name,
                        brand=brand.display_name or _("(none)")),
                }
            elif rec.audience_allow_sms and not rec.brand_allows_audience_sms:
                rec.audience_allow_sms = False
                warning = {
                    "title": _("Preset partly applied"),
                    "message": _(
                        "\"%(tmpl)s\" offers the code by SMS, but the "
                        "\"%(brand)s\" brand does not allow it (or the "
                        "channel is not configured on this instance). "
                        "Visitors will identify themselves by email.",
                        tmpl=tmpl.display_name,
                        brand=brand.display_name or _("(none)")),
                }
        return {"warning": warning} if warning else None

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
            "Up to %s MB in total from this screen. Beyond that, use the "
            "public send page: it uploads straight to storage, with no "
            "practical limit.", mb)
        for rec in self:
            rec.upload_hint = hint

    @api.depends("brand_id", "sender_email")
    def _compute_brand_warning(self):
        for rec in self:
            warning = ""
            email = email_normalize(rec.sender_email or "") or ""
            if rec.brand_id and email and not rec.brand_id._sender_allowed(email):
                warning = _(
                    "\"%(brand)s\" does not accept sends from %(email)s "
                    "(allowed-sender list). Choose another brand.",
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
                    "No brand offers the open audience at the "
                    "moment.\n\nTurn it on for the brand concerned "
                    "(Configuration › Brands › \"Open audience offered\") "
                    "and set a maximum number of visitors there. The link "
                    "can then be created from this menu."))
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
                    "The contact \"%s\" has no valid email address.",
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
                raise UserError(_("Invalid email address: %s", raw))
            if norm not in emails:
                emails.append(norm)
        if not emails:
            raise UserError(_("Add at least one recipient."))
        if len(emails) > MAX_RECIPIENTS:
            raise UserError(_("Maximum %s recipients.", MAX_RECIPIENTS))
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
                    "Your account has no email address, so a secure "
                    "transfer cannot be sent in your name. Ask an "
                    "administrator to fill in your address."))
            return (user.name or "").strip(), own
        email = email_normalize(self.sender_email or "") \
            or (self.sender_email or "").strip()
        if not email:
            raise UserError(_("A sender email address is required."))
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
                "The attached files total %(total).1f MB; this screen "
                "accepts %(max)s at most. For a larger send, use the "
                "public send page (direct upload to storage).",
                total=total / (1024 * 1024), max=ceiling // (1024 * 1024)))
        out = []
        for att in self.attachment_ids:
            data = att.raw or b""
            if not data:
                raise UserError(_(
                    "The file \"%s\" is empty or unreadable.",
                    att.name or "?"))
            # The stored file_size is metadata; the bytes are the truth. A
            # mismatch means the attachment was tampered with between upload
            # and send, so re-check the ceiling against what we actually hold.
            out.append((att.name or "fichier", data))
        if sum(len(d) for _n, d in out) > ceiling:
            raise UserError(_(
                "The attached files exceed this screen's %s MB limit.",
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
            raise UserError(_("Action restricted to secure transfer users."))
        open_audience = self.audience_mode == "open"
        if self.otp_channel == "sms" and not open_audience \
                and not sms.configured(self.env):
            raise UserError(_(
                "The SMS channel is not configured (VoIP.ms). Configure "
                "it in Configuration › Settings, or choose the Email "
                "channel."))
        if open_audience and self.audience_allow_sms and not sms.configured(self.env):
            raise UserError(_(
                "The SMS channel is not configured (VoIP.ms): visitors "
                "could not receive a code on their mobile."))

        brand = self.brand_id
        # A drop page forces every send to its own owner. It is excluded from
        # the field domain, but a domain is a UI hint — re-check it here or an
        # RPC caller redirects the transfer to that person instead.
        if brand.fixed_recipient:
            raise UserError(_(
                "\"%s\" is a drop page: it can only receive, never send.", brand.display_name))

        sender_name, sender_email = self._effective_sender()
        # -- anti-piggyback, sender side (public flow: api_create + finalize)
        if not brand._sender_allowed(sender_email):
            raise UserError(_(
                "\"%(brand)s\" does not allow sends from %(email)s.",
                brand=brand.display_name, email=sender_email))

        if open_audience:
            # La marque est le seul endroit où l'opérateur a consenti au mode.
            # La contrainte du modèle dirait la même chose, mais un message
            # d'assistant vaut mieux qu'une erreur de validation.
            if not brand.allow_open_audience:
                raise UserError(_(
                    "\"%s\" does not offer the open audience. Turn it on "
                    "for the brand, or choose \"Named recipients\".",
                    brand.display_name))
            if self.audience_allow_sms and not brand.allow_audience_sms:
                raise UserError(_(
                    "\"%s\" does not offer the code by SMS for open audiences.",
                    brand.display_name))
            # ⚠ Un plafond à 0 (illimité) sur un lien que personne ne nomme,
            # c'est un relais de courriel ouvert. On refuse plutôt que de
            # laisser l'opérateur le découvrir dans sa facture VoIP.ms ou dans
            # une plainte anti-pourriel.
            if not (self.audience_max or brand.audience_max_default):
                raise UserError(_(
                    "Set a maximum number of visitors (or set it on the "
                    "brand): without a cap, this link can send a code to "
                    "as many addresses as anyone wants."))
            # Pas de destinataires nommés : le lien revient à l'expéditeur, qui
            # le diffuse. C'est le mode « lien seul » déjà connu du modèle.
            emails, sms_map = [], {}
        else:
            emails, sms_map = self._resolve_recipients()
            # -- anti-piggyback, destination side (public flow: api_create)
            bad = [r for r in emails if not brand._recipient_allowed(r)]
            if bad:
                raise UserError(_(
                    "\"%(brand)s\" only allows sending to certain "
                    "addresses. Unauthorized recipient(s): %(bad)s",
                    brand=brand.display_name, bad=", ".join(bad)))

        files = self._collect_attachments()
        if not files and not (self.message or "").strip():
            raise UserError(_("Add at least one file or a message."))

        limits = brand._effective_limits()
        retention = self.retention_days or 7
        if retention not in limits["expiry_choices"]:
            raise UserError(_(
                "Availability period not offered by \"%(brand)s\": "
                "%(asked)s day(s) requested, while this brand offers "
                "%(choices)s.",
                brand=brand.display_name, asked=retention,
                choices=", ".join(str(c) for c in limits["expiry_choices"])))
        if self.password and not brand.allow_password:
            raise UserError(_(
                "The additional password is not offered for \"%s\".", brand.display_name))

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
            # Any English (en_US included: the only language of some
            # databases) is en_CA; it used to fall back to fr_CA, and the
            # recipients without a contact got the transfer in French.
            "locale": "en_CA" if (self.env.user.lang or "").startswith("en")
            else "fr_CA",
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
        # The log is read in the transfer's language (access certificate).
        log_env = transfer._log_env()
        if open_audience:
            transfer._log(
                "created", actor=self.env.user.login,
                note=log_env._("Open audience transfer created from the backend "
                               "by %(login)s (%(files)s file(s), %(max)s "
                               "visitor(s) at most, SMS %(sms)s)")
                % {
                    "login": self.env.user.login,
                    "files": len(files),
                    "max": self.audience_max or brand.audience_max_default,
                    "sms": log_env._("offered") if self.audience_allow_sms
                    else log_env._("not offered"),
                })
        else:
            transfer._log(
                "created", actor=self.env.user.login,
                note=log_env._("Secure transfer created from the backend by "
                               "%(login)s (%(channel)s channel, %(recipients)s "
                               "recipient(s), %(files)s file(s))")
                % {
                    "login": self.env.user.login,
                    "channel": log_env._("SMS") if self.otp_channel == "sms"
                    else log_env._("email"),
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
            "name": _("Secure transfer"),
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
