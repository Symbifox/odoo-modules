"""Bridge setting: post-maintenance feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_hosting_feedback = fields.Boolean(
        string="Feedback after maintenance",
        config_parameter="bf_cx.hosting_feedback",
        help="When a scheduled maintenance on a customer service is "
             "marked done, send a 3-emoji feedback request to the "
             "service's customer (the over-solicitation guard applies).",
    )
