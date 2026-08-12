"""Backend origination of an OTP-gated secure message.

This is the "hold the mail until identity is proven" send: an internal Odoo
user composes a message to one or more contacts; instead of the body going out
in clear, a link-only notification is sent and the content is revealed on the
branded page ONLY after the recipient enters a one-time code (delivered by
e-mail or SMS). It reuses the whole secure.transfer machinery (branding, Loi 25
access journal, expiry, purge) — it just forces the recipient-OTP gate on and
picks the delivery channel.

Message-only in this version (a secure note, e.g. a password or a sensitive
paragraph). Files ride the public /secrets flow (direct-to-S3); wiring the
backend composer to server-side S3 uploads is a later step.
"""
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..models import sms
from ..models.secure_transfer import MAX_SUBJECT_LEN

MAX_RECIPIENTS = 10


class SecureSendWizard(models.TransientModel):
    _name = "secure.transfer.send.wizard"
    _description = "Envoyer un message sécurisé (code destinataire)"

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
    message = fields.Text(
        string="Message sécurisé", required=True,
        help="Contenu retenu jusqu'à la validation du code. Texte brut, rendu "
             "échappé — jamais interprété comme HTML.",
    )
    otp_channel = fields.Selection(
        selection=[("email", "Courriel"), ("sms", "SMS")],
        string="Canal du code", default="email", required=True,
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
        help="Reçoit l'accusé d'envoi.",
    )
    sms_configured = fields.Boolean(compute="_compute_sms_configured")
    sms_hint = fields.Char(compute="_compute_sms_configured")

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

    def action_send(self):
        self.ensure_one()
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_user"):
            raise UserError(_("Action réservée aux utilisateurs du transfert sécurisé."))
        if not (self.message or "").strip():
            raise UserError(_("Le message est vide."))
        if self.otp_channel == "sms" and not sms.configured(self.env):
            raise UserError(_(
                "Le canal SMS n'est pas configuré (VoIP.ms). Configurez-le dans "
                "Configuration › Paramètres, ou choisissez le canal Courriel."))
        emails, sms_map = self._resolve_recipients()

        Transfer = self.env["secure.transfer"].sudo()
        transfer = Transfer.create({
            "brand_id": self.brand_id.id,
            "sender_name": (self.sender_name or "").strip(),
            "sender_email": email_normalize(self.sender_email or "")
            or (self.sender_email or "").strip(),
            "recipient_emails": ", ".join(emails),
            "subject": Transfer._clean_line(self.subject, MAX_SUBJECT_LEN),
            "message": (self.message or "").strip(),
            "retention_days": self.retention_days or 7,
            "force_recipient_otp": True,
            "recipient_otp_channel": self.otp_channel,
            "recipient_sms_map": json.dumps(sms_map) if sms_map else False,
            "locale": self.env.user.lang if self.env.user.lang in (
                "fr_CA", "en_CA") else "fr_CA",
            "ip_created": "backend",
            "ua_created": "backend:%s" % self.env.user.login,
            "state": "draft",
        })
        if self.password:
            transfer._set_password(self.password)
        transfer._log(
            "created", actor=self.env.user.login,
            note=_("Message sécurisé créé depuis le backend (canal %s, %s "
                   "destinataire(s))")
            % (_("SMS") if self.otp_channel == "sms" else _("courriel"),
               len(emails)))
        # Activate: rotates the token, arms expiry, sends the link-only
        # notification (secure-message template) + the sender receipt.
        transfer._activate()
        return {
            "type": "ir.actions.act_window",
            "name": _("Message sécurisé"),
            "res_model": "secure.transfer",
            "res_id": transfer.id,
            "view_mode": "form",
            "target": "current",
        }
