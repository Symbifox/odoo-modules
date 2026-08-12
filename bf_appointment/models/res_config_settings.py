import logging
import os

from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None
    _logger.warning(
        "cryptography package not installed. "
        "Nextcloud Talk credential encryption will not be available."
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_appointment_jitsi_domain = fields.Char(
        string="Domaine Jitsi",
        config_parameter="bf_appointment.jitsi_domain",
        default="meet.jit.si",
        help="Domaine du serveur Jitsi Meet (ex. meet.jit.si ou votre instance auto-hébergée).",
    )
    bf_appointment_nc_talk_base_url = fields.Char(
        string="URL de base Nextcloud Talk",
        config_parameter="bf_appointment.nc_talk_base_url",
        help="URL de base de l'instance Nextcloud (ex. https://cloud.example.com).",
    )
    bf_appointment_nc_talk_user = fields.Char(
        string="Utilisateur Nextcloud Talk",
        config_parameter="bf_appointment.nc_talk_user",
        help="Utilisateur Nextcloud pour l'authentification à l'API Talk.",
    )
    bf_appointment_nc_talk_password = fields.Char(
        string="Mot de passe Nextcloud Talk",
        help="Mot de passe d'application Nextcloud pour l'API Talk. Stocké chiffré.",
    )

    appointment_brand_name = fields.Char(
        related="company_id.appointment_brand_name", readonly=False,
    )
    appointment_brand_logo_url = fields.Char(
        related="company_id.appointment_brand_logo_url", readonly=False,
    )
    appointment_brand_website_url = fields.Char(
        related="company_id.appointment_brand_website_url", readonly=False,
    )
    appointment_brand_primary = fields.Char(
        related="company_id.appointment_brand_primary", readonly=False,
    )
    appointment_brand_dark = fields.Char(
        related="company_id.appointment_brand_dark", readonly=False,
    )
    appointment_brand_support_email = fields.Char(
        related="company_id.appointment_brand_support_email", readonly=False,
    )
    appointment_brand_support_phone = fields.Char(
        related="company_id.appointment_brand_support_phone", readonly=False,
    )
    appointment_brand_support_phone_display = fields.Char(
        related="company_id.appointment_brand_support_phone_display",
        readonly=False,
    )
    appointment_brand_privacy_url = fields.Char(
        related="company_id.appointment_brand_privacy_url", readonly=False,
    )
    appointment_brand_terms_url = fields.Char(
        related="company_id.appointment_brand_terms_url", readonly=False,
    )
    appointment_brand_tagline = fields.Char(
        related="company_id.appointment_brand_tagline", readonly=False,
    )

    def set_values(self):
        res = super().set_values()
        if self.bf_appointment_nc_talk_password:
            encrypted = self._encrypt_value(self.bf_appointment_nc_talk_password)
            self.env["ir.config_parameter"].sudo().set_param(
                "bf_appointment.nc_talk_password_encrypted", encrypted
            )
        return res

    def get_values(self):
        res = super().get_values()
        encrypted = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("bf_appointment.nc_talk_password_encrypted", "")
        )
        if encrypted:
            res["bf_appointment_nc_talk_password"] = "********"
        return res

    def _get_encryption_key(self):
        from ._crypto import get_encryption_key
        return get_encryption_key(self.env)

    def _encrypt_value(self, value):
        if not value:
            return False
        if not Fernet:
            raise UserError(
                "Le paquet 'cryptography' est requis pour stocker "
                "les identifiants de manière sécurisée. "
                "Installez-le avec: pip install cryptography"
            )
        key = self._get_encryption_key()
        f = Fernet(key.encode())
        return f.encrypt(value.encode()).decode()
