import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception
    _logger.warning(
        "cryptography package not installed. "
        "IMAP password encryption will not be available."
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_webmail_url = fields.Char(
        string="URL du webmail",
        config_parameter="bf_webmail.webmail_url",
    )
    bf_webmail_imap_host = fields.Char(
        string="Serveur IMAP",
        config_parameter="bf_webmail.imap_host",
    )
    bf_webmail_imap_port = fields.Integer(
        string="Port IMAP",
        config_parameter="bf_webmail.imap_port",
        default=993,
    )
    bf_webmail_imap_user = fields.Char(
        string="Utilisateur IMAP",
        config_parameter="bf_webmail.imap_user",
    )
    bf_webmail_imap_password = fields.Char(
        string="Mot de passe IMAP",
        compute="_compute_imap_password",
        inverse="_inverse_imap_password",
    )

    # --- Encryption helpers ---

    @staticmethod
    def _get_imap_encryption_key(env):
        """Get Fernet key from env var or odoo.conf. Never from DB."""
        if not Fernet:
            return None
        # Priority 1: environment variable
        key = os.environ.get("BF_WEBMAIL_FERNET_KEY")
        if key:
            return key.encode()
        # Priority 2: odoo.conf
        from odoo.tools import config
        key = config.get("bf_webmail_fernet_key")
        if key:
            return key.encode()
        _logger.error(
            "bf_webmail: no Fernet key configured. "
            "Set BF_WEBMAIL_FERNET_KEY env var or "
            "bf_webmail_fernet_key in odoo.conf."
        )
        return None

    @staticmethod
    def _encrypt_imap_password(env, value):
        if not value:
            return ""
        if not Fernet:
            raise UserError(
                "Le paquet 'cryptography' est requis pour stocker "
                "le mot de passe IMAP de manière sécurisée. "
                "Installez-le avec: pip install cryptography"
            )
        key = ResConfigSettings._get_imap_encryption_key(env)
        return Fernet(key).encrypt(value.encode()).decode()

    @staticmethod
    def _decrypt_imap_password(env, encrypted):
        if not encrypted:
            return ""
        key = ResConfigSettings._get_imap_encryption_key(env)
        if not key:
            _logger.warning("bf_webmail: cannot decrypt — Fernet not available")
            return ""
        try:
            return Fernet(key).decrypt(encrypted.encode()).decode()
        except InvalidToken:
            _logger.warning("bf_webmail: decryption failed (key rotation or legacy value)")
            return encrypted
        except Exception:
            _logger.exception("IMAP password decryption failed")
            return ""

    # --- Computed field ---

    def _compute_imap_password(self):
        encrypted = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_webmail.imap_password_encrypted", "")
        )
        plaintext = self._decrypt_imap_password(self.env, encrypted)
        for rec in self:
            rec.bf_webmail_imap_password = plaintext

    def _inverse_imap_password(self):
        for rec in self:
            encrypted = self._encrypt_imap_password(
                self.env, rec.bf_webmail_imap_password or ""
            )
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_webmail.imap_password_encrypted", encrypted
            )
