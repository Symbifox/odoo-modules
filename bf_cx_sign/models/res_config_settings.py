"""Bridge setting: post-signature feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_sign_feedback = fields.Boolean(
        string="Feedback après signature",
        config_parameter="bf_cx.sign_feedback",
        help="Quand une demande de signature est complétée, envoyer une "
             "demande de feedback à 3 émojis au signataire principal "
             "(le garde-fou anti-sursollicitation s'applique).",
    )
