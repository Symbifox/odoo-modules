import logging
import random
import string
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Use cryptographically secure RNG
_sysrand = random.SystemRandom()


class PrivacyConsentVerification(models.Model):
    _name = "privacy.consent.verification"
    _description = "Code de vérification 2FA"
    _order = "create_date desc"

    consent_id = fields.Many2one(
        comodel_name="privacy.consent",
        string="Consentement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    code = fields.Char(
        string="Code",
        size=6,
        required=True,
        readonly=True,
    )
    action = fields.Selection(
        selection=[
            ("grant", "Accorder"),
            ("refuse", "Refuser"),
        ],
        string="Action demandée",
        required=True,
    )
    expires_at = fields.Datetime(
        string="Expire le",
        required=True,
    )
    verified = fields.Boolean(
        string="Vérifié",
        default=False,
    )
    verified_at = fields.Datetime(
        string="Vérifié le",
    )
    ip_address = fields.Char(
        string="Adresse IP",
    )
    user_agent = fields.Char(
        string="Agent utilisateur",
        size=512,
    )
    access_type = fields.Selection(
        selection=[
            ("portal_authenticated", "Portail (authentifié)"),
            ("portal_public", "Portail (lien public)"),
        ],
        string="Type d'accès",
    )
    attempt_count = fields.Integer(
        string="Tentatives",
        default=0,
    )
    locked = fields.Boolean(
        string="Verrouillé",
        default=False,
    )

    @staticmethod
    def _generate_code():
        """Generate a cryptographically secure 6-digit code."""
        return "".join(_sysrand.choices(string.digits, k=6))

    @api.model
    def create_verification(self, consent, action, request_obj, access_type):
        """Create a verification code and send it by email.

        Rate limit: max 3 active codes per 15 min per consent.

        Args:
            consent: privacy.consent record
            action: 'grant' or 'refuse'
            request_obj: Odoo HTTP request object
            access_type: 'portal_authenticated' or 'portal_public'

        Returns:
            Created privacy.consent.verification record, or False if rate limited
        """
        Evidence = self.env["privacy.consent.evidence"]

        # Rate limit: max 3 active (non-expired, non-verified) codes per 15 min
        cutoff = fields.Datetime.now() - timedelta(minutes=15)
        active_codes = self.sudo().search_count([
            ("consent_id", "=", consent.id),
            ("create_date", ">=", cutoff),
            ("verified", "=", False),
        ])
        if active_codes >= 3:
            _logger.warning(
                "Rate limit: consent %s already has %d active codes in 15 min",
                consent.id, active_codes,
            )
            return False

        # Get client info
        httprequest = request_obj.httprequest
        client_ip = Evidence._get_client_ip(httprequest)
        user_agent = str(httprequest.user_agent)[:512] if httprequest.user_agent else ""

        code = self._generate_code()
        verification = self.sudo().create({
            "consent_id": consent.id,
            "code": code,
            "action": action,
            "expires_at": fields.Datetime.now() + timedelta(minutes=15),
            "ip_address": client_ip,
            "user_agent": user_agent,
            "access_type": access_type,
        })

        # Send verification email
        verification._send_verification_email()

        return verification

    def verify_code(self, submitted_code):
        """Verify the submitted code.

        Returns:
            'valid' if code matches
            'invalid' if code is wrong (with remaining attempts)
            'expired' if code has expired
            'locked' if too many attempts (5 max)
        """
        self.ensure_one()

        if self.locked:
            return "locked"

        if self.verified:
            return "valid"

        if fields.Datetime.now() > self.expires_at:
            return "expired"

        if self.attempt_count >= 5:
            self.sudo().write({"locked": True})
            return "locked"

        self.sudo().write({"attempt_count": self.attempt_count + 1})

        if submitted_code == self.code:
            self.sudo().write({
                "verified": True,
                "verified_at": fields.Datetime.now(),
            })
            return "valid"

        # Check if now locked after this attempt
        if self.attempt_count >= 5:
            self.sudo().write({"locked": True})
            return "locked"

        return "invalid"

    def _send_verification_email(self):
        """Send the verification code by email."""
        self.ensure_one()
        template = self.env.ref(
            "privacy_consent.mail_template_verification_code",
            raise_if_not_found=False,
        )
        if template:
            template.sudo().send_mail(self.id, force_send=True)
        else:
            _logger.error("Verification email template not found")

    @api.model
    def _cleanup_expired(self):
        """Cron: delete verification codes older than 24h."""
        cutoff = fields.Datetime.now() - timedelta(hours=24)
        expired = self.sudo().search([
            ("create_date", "<", cutoff),
        ])
        count = len(expired)
        if count:
            expired.unlink()
            _logger.info("Cleaned up %d expired verification codes", count)

    @property
    def remaining_attempts(self):
        """Return number of remaining attempts."""
        return max(0, 5 - self.attempt_count)
