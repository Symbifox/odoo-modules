from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gamification_leave_type_id = fields.Many2one(
        'hr.leave.type',
        string="Type de congé",
        config_parameter='bf_gamification.leave_type_id',
        help="Le type de congé des allocations créées quand un congé de "
             "constance est approuvé.",
    )
