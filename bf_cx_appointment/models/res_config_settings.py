"""Bridge setting: post-appointment feedback request."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_appointment_feedback = fields.Boolean(
        string="Feedback after an appointment",
        config_parameter="bf_cx.appointment_feedback",
        help="When an appointment is over, send a 3-emoji feedback "
             "request to the appointment's contact (the over-solicitation "
             "guard applies).",
    )
