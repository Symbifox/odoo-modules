import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning("cryptography package not installed. LibreSign credential encryption will not be available.")


class PrivacyLibresignConfig(models.Model):
    """Configuration pour l'intégration avec l'API LibreSign (Nextcloud)."""

    _name = "privacy.libresign.config"
    _description = "Configuration LibreSign"
    _rec_name = "name"

    name = fields.Char(
        string="Nom",
        required=True,
        default="Configuration LibreSign",
    )
    active = fields.Boolean(default=True)

    # Nextcloud / LibreSign Configuration
    nextcloud_url = fields.Char(
        string="URL Nextcloud",
        required=True,
        default="",
        help="URL de base de l'instance Nextcloud (ex. : https://nextcloud.exemple.com)",
    )
    username = fields.Char(
        string="Nom d'utilisateur",
        required=True,
        help="Nom d'utilisateur Nextcloud pour l'authentification à l'API LibreSign",
    )
    password = fields.Char(
        string="Mot de passe",
        compute="_compute_password",
        inverse="_inverse_password",
        store=False,
        help="Mot de passe Nextcloud (chiffré)",
    )
    password_encrypted = fields.Char(
        string="Mot de passe (chiffré)",
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
        help="URL à configurer dans LibreSign pour les webhooks",
    )

    # Default Settings
    auto_send = fields.Boolean(
        string="Envoi automatique des demandes",
        default=True,
        help="Envoyer automatiquement la demande de signature après la création",
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
            "Une seule configuration LibreSign par société est autorisée !",
        ),
    ]

    # === Encryption Methods (shared key with DocuSeal) ===

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
            _logger.debug("Value appears to be unencrypted, returning as-is")
            return encrypted_value
        except Exception as e:
            _logger.error("Decryption failed: %s", e)
            return encrypted_value

    # === Computed Fields ===

    def _compute_password(self):
        """Decrypt password for display."""
        for record in self:
            record.password = record._decrypt_value(record.password_encrypted)

    def _inverse_password(self):
        """Encrypt password on write."""
        for record in self:
            if record.password:
                record.password_encrypted = record._encrypt_value(record.password)

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
        """Compute webhook URL for LibreSign configuration."""
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        for record in self:
            record.webhook_url = f"{base_url}/privacy/libresign/webhook"

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
        """Test the LibreSign API connection."""
        self.ensure_one()
        Interface = self.env["privacy.libresign.interface"]
        result = Interface.test_connection(self)
        if result.get("success"):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Connexion réussie",
                    "message": "Connexion à l'API LibreSign établie avec succès.",
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
