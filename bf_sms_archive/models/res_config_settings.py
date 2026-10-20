from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_sms_voipms_enabled = fields.Boolean(
        string="Enable VOIP.ms messaging",
        config_parameter="bf_sms_archive.voipms_enabled",
    )
    bf_sms_voipms_api_username = fields.Char(
        string="VOIP.ms: API user",
        config_parameter="bf_sms_archive.voipms_api_username",
    )
    bf_sms_voipms_api_password = fields.Char(
        string="VOIP.ms: API password",
        config_parameter="bf_sms_archive.voipms_api_password",
    )
    bf_sms_public_base_url = fields.Char(
        string="Public URL (webhook)",
        config_parameter="bf_sms_archive.public_base_url",
        help="Public HTTPS base for the VOIP.ms callback URL (e.g. "
             "https://odoo.example.com).",
    )
    bf_sms_voipms_tz = fields.Char(
        string="VOIP.ms: account time zone",
        config_parameter="bf_sms_archive.voipms_tz",
        help="Time zone in which VOIP.ms issues its timestamps (\"Time "
             "Zone\" setting of the VOIP.ms portal, US/Eastern by "
             "default). IANA name, e.g. America/New_York.",
    )
