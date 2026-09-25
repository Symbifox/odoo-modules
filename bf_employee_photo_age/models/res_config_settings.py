from odoo import fields, models

from .hr_employee import DEFAULT_MONTHS, PARAM_MONTHS


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_photo_months = fields.Integer(
        string="Photo reminder after (months)", config_parameter=PARAM_MONTHS, default=DEFAULT_MONTHS,
        help="Remind an employee to update a photo older than this. 0 turns the reminder off.")
