import hashlib
import hmac
import re
import secrets
import uuid
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Fields the signing/sending flow may write once the request has left draft.
# Identity fields (name, email, partner_id, sequence) are frozen after sending.
_PROCESS_FIELDS = frozenset({
    "state", "signed_on", "signer_ip", "signer_user_agent",
    # Opening the document happens after sending, by definition.
    "first_viewed_on", "last_viewed_on", "view_count", "has_viewed",
    # So does being invited and chased.
    "invited_on", "reminder_count", "last_reminder_on", "unopened_alerted",
    "consent_given", "consent_timestamp", "signature_image", "initials_image",
    "otp_hash", "otp_sent_at", "otp_verified", "otp_attempts", "otp_send_count",
})

OTP_LENGTH = 6
OTP_TTL = 600          # seconds a code stays valid
OTP_MAX_ATTEMPTS = 5   # verify tries before a resend is required
OTP_RESEND_COOLDOWN = 30  # seconds between code sends
OTP_MAX_SENDS = 10     # total codes per signer (anti email-bombing)


class BfSignSigner(models.Model):
    """One signer on a signature request.

    Each signer has their own access token (their personal signing link) and
    their own state, so multi-signer requests (parallel or sequential) work.
    """

    _name = "bf.sign.signer"
    _description = "Signataire"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "bf.sign.request", required=True, ondelete="cascade", index=True,
    )
    name = fields.Char(string="Nom", required=True)
    email = fields.Char(string="Courriel", required=True)
    partner_id = fields.Many2one("res.partner", string="Contact")
    sequence = fields.Integer(string="Ordre", default=10)
    color = fields.Integer(string="Couleur")

    # The personal signing token is the signer's identity factor: it must NOT be
    # readable by the requester (a basic sign user), otherwise they could open
    # the link and sign on the signer's behalf. Restricted to managers; the
    # public controller reads it via sudo, and the invitation email is rendered
    # under sudo (see bf.sign.request._email_signer).
    access_token = fields.Char(
        default=lambda self: str(uuid.uuid4()), copy=False, index=True, readonly=True,
        groups="bf_sign.group_sign_manager",
    )
    state = fields.Selection(
        selection=[
            ("pending", "En attente"),
            ("viewed", "Consulté"),
            ("signed", "Signé"),
            ("refused", "Refusé"),
        ],
        string="État", default="pending", copy=False,
    )
    signed_on = fields.Datetime(readonly=True, copy=False)
    # Opening the document is tracked on the record itself, not only in the
    # audit trail: "has this person even looked at it yet" is a follow-up
    # question, and it should not require reading the journal to answer.
    # ``state`` alone cannot answer it either — it moves on to "signed".
    first_viewed_on = fields.Datetime(
        string="Ouvert le", readonly=True, copy=False,
        help="Première ouverture du document par ce signataire.")
    last_viewed_on = fields.Datetime(
        string="Dernière ouverture", readonly=True, copy=False)
    view_count = fields.Integer(string="Ouvertures", readonly=True, copy=False, default=0)
    has_viewed = fields.Boolean(
        string="A ouvert", compute="_compute_has_viewed", store=True)
    # Reminders are counted from the moment THIS signer was invited, not from
    # the send: in a sequential request the second signer is invited days later,
    # and chasing them on the first signer's clock would be nonsense.
    invited_on = fields.Datetime(string="Invité le", readonly=True, copy=False)
    reminder_count = fields.Integer(string="Relances", readonly=True, copy=False, default=0)
    last_reminder_on = fields.Datetime(string="Dernière relance", readonly=True, copy=False)
    unopened_alerted = fields.Boolean(readonly=True, copy=False)
    signer_ip = fields.Char(string="Adresse IP", readonly=True, copy=False)
    signer_user_agent = fields.Char(string="Agent utilisateur", readonly=True, copy=False)
    consent_given = fields.Boolean(readonly=True, copy=False)
    consent_timestamp = fields.Datetime(readonly=True, copy=False)
    signature_image = fields.Binary(string="Signature", readonly=True, copy=False)
    initials_image = fields.Binary(string="Paraphe", readonly=True, copy=False)

    # Email OTP (identity check at signing time — gated by request.require_signer_otp).
    otp_hash = fields.Char(copy=False, groups="bf_sign.group_sign_manager")
    otp_sent_at = fields.Datetime(copy=False)
    otp_verified = fields.Boolean(copy=False)
    otp_attempts = fields.Integer(copy=False, default=0)
    otp_send_count = fields.Integer(copy=False, default=0)

    field_ids = fields.One2many("bf.sign.field", "signer_id", string="Pavés")
    field_count = fields.Integer(compute="_compute_field_count")
    signing_url = fields.Char(
        string="Lien de signature", compute="_compute_signing_url",
        groups="bf_sign.group_sign_manager")

    @api.depends("access_token", "request_id")
    def _compute_signing_url(self):
        for rec in self:
            rec_s = rec.sudo()
            if rec_s.request_id and rec_s.id and rec_s.access_token:
                rec.signing_url = rec_s._signing_url()
            else:
                rec.signing_url = False
    has_initials = fields.Boolean(compute="_compute_field_kinds")
    has_signature = fields.Boolean(compute="_compute_field_kinds")

    @api.depends("field_ids")
    def _compute_field_count(self):
        for rec in self:
            rec.field_count = len(rec.field_ids)

    @api.depends("first_viewed_on")
    def _compute_has_viewed(self):
        for rec in self:
            rec.has_viewed = bool(rec.first_viewed_on)

    @api.depends("field_ids.field_type")
    def _compute_field_kinds(self):
        for rec in self:
            types = rec.field_ids.mapped("field_type")
            rec.has_initials = "initials" in types
            rec.has_signature = "signature" in types

    # ── Structural lock: recipients are frozen once the request leaves draft ────
    @staticmethod
    def _assert_draft(requests):
        locked = requests.filtered(lambda r: r.state != "draft")
        if locked:
            raise UserError(_(
                "Les destinataires ne peuvent être ajoutés, modifiés ou retirés "
                "qu'en brouillon. Remettez la demande en brouillon pour la modifier."))

    @api.model_create_multi
    def create(self, vals_list):
        reqs = self.env["bf.sign.request"].browse(
            [v.get("request_id") for v in vals_list if v.get("request_id")])
        self._assert_draft(reqs.exists())
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) - _PROCESS_FIELDS:
            self._assert_draft(self.request_id)
        return super().write(vals)

    def unlink(self):
        self._assert_draft(self.request_id)
        return super().unlink()

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            if not self.name:
                self.name = self.partner_id.name
            if not self.email:
                self.email = self.partner_id.email

    def _signing_url(self):
        self.ensure_one()
        base = self.request_id._get_base_url()
        return "%s/sign/%s/%s" % (base, self.request_id.id, self.access_token)

    def action_resend_invitation(self):
        """Re-send the signing invitation to THIS signer only.

        Re-running ``action_send`` on the whole request was the only way to get
        an invitation out again, which re-mails everyone — including people who
        have already signed.

        Gated on ``_signer_can_sign`` so a sequential request never invites
        someone out of turn: receiving a link they cannot use reads as a broken
        system, and it discloses the request to a party whose turn has not come.
        """
        self.ensure_one()
        request = self.request_id
        if self.state == "signed":
            raise UserError(_("%s a déjà signé.") % self.name)
        if self.state == "refused":
            raise UserError(_("%s a refusé de signer.") % self.name)
        if not request._signer_can_sign(self):
            if request.signing_order == "sequential":
                raise UserError(_(
                    "La signature est séquentielle et ce n'est pas au tour de "
                    "%s. Relancez plutôt le signataire courant.") % self.name)
            raise UserError(_(
                "La demande doit être envoyée et encore ouverte pour relancer "
                "un signataire."))
        # Same debounce as the request-level reminder: the button is a human
        # decision, but nothing should let it mail the same person in a loop.
        if self.last_reminder_on and (
                fields.Datetime.now() - self.last_reminder_on) < timedelta(hours=1):
            raise UserError(_(
                "%s vient d'être relancé. Réessayez dans une heure.") % self.name)
        request._email_signer(self, "bf_sign.mail_template_sign_reminder",
                              mark_invited=False)
        self.sudo().write({
            "reminder_count": self.reminder_count + 1,
            "last_reminder_on": fields.Datetime.now(),
        })
        self.env["bf.sign.log"]._append(
            request, "sent", actor=self.env.user.name,
            identity_method="internal_user",
            note=_("Invitation renvoyée à %s (%s)") % (self.name, self.email))
        return True

    def action_reveal_signing_link(self):
        """Manager-only break-glass: open the reveal wizard, which first warns
        and only reveals + logs on explicit confirmation (so the manager can
        cancel). Reveal-only — it never opens the link (that would register a
        view as the signer)."""
        self.ensure_one()
        if not self.env.user.has_group("bf_sign.group_sign_manager"):
            raise UserError(_("Action réservée aux gestionnaires de signature."))
        wizard = self.env["bf.sign.reveal.link.wizard"].create({"signer_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Copier le lien de signature"),
            "res_model": "bf.sign.reveal.link.wizard",
            "view_mode": "form",
            "res_id": wizard.id,
            "target": "new",
        }

    def _overlay_fields(self):
        """This signer's placed pads, in the order they are presented to them.

        Drives the numbered placement markers drawn on the public signing page:
        the index in this ordering is the human-facing identifier (1, 2, 3…)
        shown both on the document overlay and next to the matching input.

        ``sequence`` comes first so the preparer can impose an order that the
        geometry does not give — two pads side by side, or a signature that
        must be reached last. It defaults to the same value on every pad, so
        untouched requests keep falling back to reading order (page, top, left)
        exactly as before.
        """
        self.ensure_one()
        return self.field_ids.sorted(
            key=lambda f: (f.page, f.sequence, round(f.pos_y, 4), round(f.pos_x, 4), f.id))

    def _default_initials(self):
        """Initials derived from the signer's name, used to pre-fill the typed
        « paraphe » input on the signing page (the signer can still edit them or
        switch back to drawing). « Marie Tremblay » → « MT »."""
        self.ensure_one()
        parts = re.split(r"[\s\-]+", (self.name or "").strip())
        letters = [p[0] for p in parts if p and p[0].isalpha()]
        return "".join(letters[:4]).upper()

    # ── Email OTP (identity check at signing time) ───────────────────────────
    def _identity_method(self):
        """Identity method recorded in the audit trail for this signer."""
        self.ensure_one()
        return "email_otp" if self.otp_verified else "email_link_token"

    def _otp_required(self):
        """Whether this signer must verify an emailed code before signing."""
        self.ensure_one()
        return bool(self.request_id.require_signer_otp) and not self.otp_verified

    def _otp_code_hash(self, code):
        # Bind the code to the signer's token; the real guards for a 6-digit
        # code are the attempt cap + expiry below.
        return hashlib.sha256(
            ("%s:%s" % (code or "", self.access_token or "")).encode()).hexdigest()

    def _otp_can_resend(self):
        self.ensure_one()
        if not self.otp_sent_at:
            return True
        return (fields.Datetime.now() - self.otp_sent_at).total_seconds() >= OTP_RESEND_COOLDOWN

    def _otp_send(self, force=False):
        """Generate a fresh code, store only its hash, and email it. Returns
        False if the resend cap is reached, or a code was just sent (cooldown)
        and ``force`` is not set."""
        self.ensure_one()
        if self.otp_send_count >= OTP_MAX_SENDS:
            return False  # hard cap — prevents email-bombing the signer's inbox
        if not force and not self._otp_can_resend():
            return False
        code = "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))
        self.sudo().write({
            "otp_hash": self._otp_code_hash(code),
            "otp_sent_at": fields.Datetime.now(),
            "otp_attempts": 0,
            "otp_send_count": self.otp_send_count + 1,
        })
        self._otp_email(code)
        return True

    def _otp_email(self, code):
        self.ensure_one()
        company = self.request_id.company_id
        primary = company.report_brand_primary or "#29ABE1"
        dark = company.report_brand_dark or "#2D3031"
        # White/light logo on the dark header when configured, else the standard logo.
        logo_field = "report_brand_logo" if company.report_brand_logo else "logo"
        body = (
            '<div style="font-family:Lexend,system-ui,Arial,sans-serif;color:#2D3031;'
            'font-size:14px;line-height:1.55;max-width:600px;margin:0 auto;">'
            '<div style="background:%(dark)s;padding:18px 24px;border-radius:10px 10px 0 0;">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;"><tbody><tr>'
            '<td align="left" style="vertical-align:middle;">'
            '<img src="/web/image/res.company/%(cid)s/%(lf)s" alt="" style="height:36px;display:block;border:0;"/>'
            '</td><td align="right" style="vertical-align:middle;color:#fff;'
            'font-family:Lexend,system-ui,Arial,sans-serif;font-size:20px;font-weight:700;">'
            'Code de vérification</td></tr></tbody></table>'
            '</div><div style="height:4px;background:%(primary)s;"></div>'
            '<div style="background:#fff;border:1px solid #e3e7eb;border-top:0;padding:24px;'
            'border-radius:0 0 10px 10px;"><p>Bonjour %(name)s,</p>'
            '<p>Voici votre code pour consulter et signer le document '
            '<strong>%(doc)s</strong>&nbsp;:</p>'
            '<p style="font-size:30px;font-weight:700;letter-spacing:6px;color:%(primary)s;'
            'margin:18px 0;">%(code)s</p>'
            '<p style="color:#777;font-size:12px;">Ce code expire dans 10&nbsp;minutes et ne '
            'doit être partagé avec personne.</p></div></div>'
        ) % {"dark": dark, "primary": primary, "cid": company.id, "lf": logo_field,
             "name": self.name or "", "doc": self.request_id.name or "", "code": code}
        mail = self.env["mail.mail"].sudo().create({
            "subject": _("Code de vérification : %s") % (self.request_id.name or ""),
            "email_from": company.email_formatted or self.env.user.email_formatted,
            "email_to": self.email,
            "body_html": body,
            "auto_delete": True,
        })
        mail.send()

    def _otp_verify(self, code):
        """True (and marks verified) on a correct, unexpired, non-locked code."""
        self.ensure_one()
        if not self.otp_hash or not self.otp_sent_at:
            return False
        if (fields.Datetime.now() - self.otp_sent_at).total_seconds() > OTP_TTL:
            return False
        if self.otp_attempts >= OTP_MAX_ATTEMPTS:
            return False
        if hmac.compare_digest(self.otp_hash, self._otp_code_hash((code or "").strip())):
            self.sudo().write({"otp_verified": True})
            return True
        self.sudo().write({"otp_attempts": self.otp_attempts + 1})
        return False
