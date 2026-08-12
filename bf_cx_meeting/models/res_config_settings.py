"""Bridge setting: post-report feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_meeting_feedback = fields.Boolean(
        string="Feedback après compte rendu",
        config_parameter="bf_cx.meeting_feedback",
        help="Après l'envoi d'un compte rendu de rencontre au client, "
             "envoyer une demande de feedback à 3 émojis au partenaire du "
             "projet (le garde-fou anti-sursollicitation s'applique).",
    )
