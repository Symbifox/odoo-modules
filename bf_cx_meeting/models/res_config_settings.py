"""Bridge setting: post-report feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_meeting_feedback = fields.Boolean(
        string="Feedback after a meeting report",
        config_parameter="bf_cx.meeting_feedback",
        help="After a meeting report is sent to the customer, send a "
             "3-emoji feedback request to the project's customer (the "
             "over-solicitation guard applies).",
    )
