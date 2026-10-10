"""Bridge setting: post-signature feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_sign_feedback = fields.Boolean(
        string="Feedback after a signature",
        config_parameter="bf_cx.sign_feedback",
        help="When a signature request is completed, send a 3-emoji "
             "feedback request to the main signer (the over-solicitation "
             "guard applies).",
    )
