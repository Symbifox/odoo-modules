from odoo import fields, models


class PersonalBudgetContributor(models.Model):
    _name = 'personal.budget.contributor'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Contributor'

    name = fields.Char(string="Name", required=True)
    is_default = fields.Boolean(string="Default")
    active = fields.Boolean(string="Active", default=True)
