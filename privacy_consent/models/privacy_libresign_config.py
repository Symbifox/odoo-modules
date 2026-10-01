import logging
import os

from odoo import api, fields, models
from odoo.tools import config

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken, MultiFernet
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
        groups="privacy_consent.group_privacy_manager",
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
        groups="privacy_consent.group_privacy_manager",
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

    # La clé se range hors de la base (odoo.conf, puis variable
    # d'environnement), comme celle du coffre bf_credentials. C'est la MÊME clé que
    # l'ancien paramètre système, reprise telle quelle : rien n'est re-chiffré. Sans
    # clé hors base, on retombe sur le paramètre en base, avec un avertissement.
    _CLE_ENV = "BF_PRIVACY_CONSENT_FERNET_KEY"
    _CLE_CONF = "privacy_consent_fernet_key"
    _CLE_PARAM = "privacy_consent.encryption_key"

    def _cle_hors_base(self):
        cle = config.get(self._CLE_CONF) or os.environ.get(self._CLE_ENV)
        return cle.encode() if cle else None

    def _fernet_lecture(self):
        """Déchiffre avec la clé hors base, puis avec l'ancienne clé en base."""
        cles = [self._get_encryption_key()]
        ancienne = self.env["ir.config_parameter"].sudo().get_param(self._CLE_PARAM)
        if ancienne and ancienne.encode() not in cles:
            cles.append(ancienne.encode())
        return MultiFernet([Fernet(c) for c in cles])

    def _get_encryption_key(self):
        """Clé de chiffrement : hors base d'abord, sinon le paramètre système."""
        if not Fernet:
            return None
        hors_base = self._cle_hors_base()
        if hors_base:
            return hors_base
        ICP = self.env["ir.config_parameter"].sudo()
        key = ICP.get_param("privacy_consent.encryption_key")
        _logger.warning(
            "Clé de chiffrement lue dans le paramètre système %s : rangez-la dans "
            "%s de odoo.conf.", self._CLE_PARAM, self._CLE_CONF)
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
            f = self._fernet_lecture()
            return f.decrypt(encrypted_value.encode()).decode()
        except InvalidToken:
            _logger.debug("Value appears to be unencrypted, returning as-is")
            return encrypted_value
        except Exception as e:
            _logger.error("Decryption failed: %s", e)
            return encrypted_value

    # === Computed Fields ===
    #
    # ⚠ Le champ en clair est réservé aux gestionnaires vie privée (ceux qui
    # voient le menu Configuration et ont l'écriture sur ce modèle) : l'ACL
    # donne la lecture au simple utilisateur vie privée, qui lisait donc le
    # secret déchiffré. La colonne chiffrée reste réservée à
    # l'administrateur ; le calcul et l'inverse y passent en sudo, une fois
    # le contrôle de groupe du champ en clair franchi.

    def _compute_password(self):
        """Decrypt password for display."""
        for record in self:
            record.password = record._decrypt_value(record.sudo().password_encrypted)

    def _inverse_password(self):
        """Encrypt password on write."""
        for record in self:
            if record.password:
                record.sudo().password_encrypted = record._encrypt_value(record.password)

    def _compute_webhook_secret(self):
        """Decrypt webhook secret for display."""
        for record in self:
            record.webhook_secret = record._decrypt_value(record.sudo().webhook_secret_encrypted)

    def _inverse_webhook_secret(self):
        """Encrypt webhook secret on write."""
        for record in self:
            if record.webhook_secret:
                record.sudo().webhook_secret_encrypted = record._encrypt_value(record.webhook_secret)

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
