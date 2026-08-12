import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning("cryptography package not installed. DocuSeal API key encryption will not be available.")


class PrivacyDocusealConfig(models.Model):
    """Configuration pour l'intégration avec l'API DocuSeal."""

    _name = "privacy.docuseal.config"
    _description = "Configuration DocuSeal"
    _rec_name = "name"

    name = fields.Char(
        string="Nom",
        required=True,
        default="Configuration DocuSeal",
    )
    active = fields.Boolean(default=True)

    # API Configuration
    api_url = fields.Char(
        string="URL de l'API",
        required=True,
        default="https://api.docuseal.co",
        help="URL de base de l'API DocuSeal",
    )
    api_key = fields.Char(
        string="Clé API",
        compute="_compute_api_key",
        inverse="_inverse_api_key",
        store=False,
        help="Clé API DocuSeal (chiffrée)",
    )
    api_key_encrypted = fields.Char(
        string="Clé API (chiffrée)",
        groups="base.group_system",
    )

    # Webhook Configuration
    webhook_secret = fields.Char(
        string="Secret du webhook",
        compute="_compute_webhook_secret",
        inverse="_inverse_webhook_secret",
        store=False,
        help="Secret pour la vérification de la signature du webhook",
    )
    webhook_secret_encrypted = fields.Char(
        string="Secret du webhook (chiffré)",
        groups="base.group_system",
    )
    webhook_url = fields.Char(
        string="URL du webhook",
        compute="_compute_webhook_url",
        help="URL à configurer dans DocuSeal pour les webhooks",
    )

    # Default Settings
    default_sender_email = fields.Char(
        string="Courriel d'expéditeur par défaut",
        help="Courriel par défaut utilisé comme expéditeur dans DocuSeal",
    )
    auto_send = fields.Boolean(
        string="Envoi automatique des soumissions",
        default=True,
        help="Envoyer automatiquement la soumission pour signature après la création",
    )

    # Company
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Société",
        default=lambda self: self.env.company,
        required=True,
    )

    _sql_constraints = [
        (
            "company_uniq",
            "unique(company_id)",
            "Une seule configuration DocuSeal par société est autorisée !",
        ),
    ]

    # === Encryption Methods ===

    def _get_encryption_key(self):
        """Get or generate encryption key from system parameters."""
        if not Fernet:
            return None
        ICP = self.env["ir.config_parameter"].sudo()
        key = ICP.get_param("privacy_consent.encryption_key")
        if not key:
            key = Fernet.generate_key().decode()
            ICP.set_param("privacy_consent.encryption_key", key)
        return key.encode()

    def _encrypt_value(self, value):
        """Encrypt a string value using Fernet symmetric encryption."""
        if not value:
            return False
        key = self._get_encryption_key()
        if not key:
            _logger.warning("Encryption key not available, storing value as-is")
            return value
        try:
            f = Fernet(key)
            return f.encrypt(value.encode()).decode()
        except Exception as e:
            _logger.error("Encryption failed: %s", e)
            return value

    def _decrypt_value(self, encrypted_value):
        """Decrypt a Fernet-encrypted value."""
        if not encrypted_value:
            return False
        key = self._get_encryption_key()
        if not key:
            return encrypted_value
        try:
            f = Fernet(key)
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            # Value might not be encrypted (legacy data)
            _logger.debug("Value appears to be unencrypted, returning as-is")
            return encrypted_value
        except Exception as e:
            _logger.error("Decryption failed: %s", e)
            return encrypted_value

    # === Computed Fields ===

    def _compute_api_key(self):
        """Decrypt API key for display."""
        for record in self:
            record.api_key = record._decrypt_value(record.api_key_encrypted)

    def _inverse_api_key(self):
        """Encrypt API key on write."""
        for record in self:
            if record.api_key:
                record.api_key_encrypted = record._encrypt_value(record.api_key)

    def _compute_webhook_secret(self):
        """Decrypt webhook secret for display."""
        for record in self:
            record.webhook_secret = record._decrypt_value(record.webhook_secret_encrypted)

    def _inverse_webhook_secret(self):
        """Encrypt webhook secret on write."""
        for record in self:
            if record.webhook_secret:
                record.webhook_secret_encrypted = record._encrypt_value(record.webhook_secret)

    @api.depends("company_id")
    def _compute_webhook_url(self):
        """Compute webhook URL for DocuSeal configuration."""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for record in self:
            record.webhook_url = f"{base_url}/privacy/docuseal/webhook"

    # === Helper Methods ===

    @api.model
    def get_config(self, company_id=None):
        """Get the active configuration for a company."""
        if company_id is None:
            company_id = self.env.company.id
        return self.search([
            ("company_id", "=", company_id),
            ("active", "=", True),
        ], limit=1)

    def action_test_connection(self):
        """Test the DocuSeal API connection."""
        self.ensure_one()
        Interface = self.env["privacy.docuseal.interface"]
        result = Interface.test_connection(self)
        if result.get("success"):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Connexion réussie",
                    "message": "Connexion à l'API DocuSeal établie avec succès.",
                    "type": "success",
                    "sticky": False,
                },
            }
        else:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Échec de connexion",
                    "message": result.get("error", "Erreur inconnue"),
                    "type": "danger",
                    "sticky": True,
                },
            }
