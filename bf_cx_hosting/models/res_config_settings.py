"""Bridge setting: post-maintenance feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_hosting_feedback = fields.Boolean(
        string="Feedback après maintenance",
        config_parameter="bf_cx.hosting_feedback",
        help="Quand une maintenance planifiée touchant un service client "
             "est marquée faite, envoyer une demande de feedback à 3 émojis "
             "au client du service (le garde-fou anti-sursollicitation "
             "s'applique).",
    )
