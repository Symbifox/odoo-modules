"""Bridge setting: automatic detractor ticket."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_auto_ticket = fields.Boolean(
        string="Automatic ticket for detractors",
        config_parameter="bf_cx.auto_ticket",
        help="In addition to the follow-up activity, automatically create "
             "a helpdesk ticket (Complaints team) for each detractor or "
             "dissatisfied rating.",
    )
