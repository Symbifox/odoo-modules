from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from ..models.book import REFUS_BUDGET


class PersonalBudgetBulkCategory(models.TransientModel):
    _name = 'personal.budget.bulk.category'
    _description = "Assign a category to several transactions"

    category_id = fields.Many2one(
        'personal.budget.category', string="Category", required=True,
        ondelete='cascade',
    )
    transaction_count = fields.Integer(
        string="Selected transactions", readonly=True,
    )

    @api.constrains('category_id')
    def _check_category_readable(self):
        # Une catégorie d'un budget qu'on ne voit pas : refus, sans la nommer.
        for wizard in self:
            if wizard.category_id and not wizard.category_id.has_access('read'):
                raise AccessError(_(REFUS_BUDGET))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        res['transaction_count'] = len(self.env.context.get('active_ids') or [])
        return res

    def action_apply(self):
        self.ensure_one()
        active_ids = self.env.context.get('active_ids') or []
        if active_ids and self.category_id:
            self.env['personal.budget.transaction'].browse(active_ids).write({
                'category_id': self.category_id.id,
            })
        return {'type': 'ir.actions.act_window_close'}
