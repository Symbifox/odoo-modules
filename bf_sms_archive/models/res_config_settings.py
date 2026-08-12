from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_sms_voipms_enabled = fields.Boolean(
        string="Activer la messagerie VOIP.ms",
        config_parameter="bf_sms_archive.voipms_enabled",
    )
    bf_sms_voipms_api_username = fields.Char(
        string="VOIP.ms — utilisateur API",
        config_parameter="bf_sms_archive.voipms_api_username",
    )
    bf_sms_voipms_api_password = fields.Char(
        string="VOIP.ms — mot de passe API",
        config_parameter="bf_sms_archive.voipms_api_password",
    )
    bf_sms_public_base_url = fields.Char(
        string="URL publique (webhook)",
        config_parameter="bf_sms_archive.public_base_url",
        help="Base HTTPS publique pour l'URL de callback VOIP.ms "
             "(ex. https://odoo.exemple.com).",
    )
    bf_sms_voipms_tz = fields.Char(
        string="VOIP.ms — fuseau du compte",
        config_parameter="bf_sms_archive.voipms_tz",
        help="Fuseau dans lequel VOIP.ms émet ses horodatages (réglage « Time "
             "Zone » du portail VOIP.ms, US/Eastern par défaut). Nom IANA, "
             "ex. America/New_York.",
    )
