from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    unblock_notify_manager = fields.Boolean(
        related='company_id.unblock_notify_manager', readonly=False)
    unblock_gen_plan = fields.Boolean(
        related='company_id.unblock_gen_plan', readonly=False)
    unblock_gen_available = fields.Boolean(
        string='Game plan available', compute='_compute_unblock_gen_available')

    @api.depends('company_id')
    def _compute_unblock_gen_available(self):
        # Gen installed AND the database allowed to use the plan: a client's
        # administrator does not see a setting their bridge would refuse.
        available = self.env['bf.task.unblock.event']._plan_available()
        for settings in self:
            settings.unblock_gen_available = available
