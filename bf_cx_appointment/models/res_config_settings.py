"""Bridge setting: post-appointment feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_appointment_feedback = fields.Boolean(
        string="Feedback après rendez-vous",
        config_parameter="bf_cx.appointment_feedback",
        help="Quand un rendez-vous est terminé, envoyer une demande de "
             "feedback à 3 émojis au contact du rendez-vous (le garde-fou "
             "anti-sursollicitation s'applique).",
    )
