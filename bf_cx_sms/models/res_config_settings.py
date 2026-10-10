"""Bridge settings: SMS survey invitations."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_sms_invite = fields.Boolean(
        string="Survey invitations by SMS",
        config_parameter="bf_cx.sms_invite",
        help="Enables the \"Invite by SMS\" button on waves to reach "
             "recipients without an email address. Manual action only (no "
             "scheduled job), at most 5 SMS per click.",
    )
    bf_cx_sms_line_id = fields.Many2one(
        "sms.archive.line",
        string="Sending SMS line",
        config_parameter="bf_cx.sms_line_id",
        domain=[("active", "=", True), ("sms_enabled", "=", True)],
        help="Line used for SMS invitations. Empty = first active line "
             "(the default line is preferred).",
    )
