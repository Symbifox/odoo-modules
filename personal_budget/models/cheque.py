from odoo import fields, models


class PersonalBudgetCheque(models.Model):
    _name = 'personal.budget.cheque'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Cheque'
    _order = 'number desc'

    number = fields.Char(string="Number", required=True)
    date = fields.Date(string="Date")
    payee = fields.Char(string="Payee")
    amount = fields.Float(string="Amount", digits=(12, 2))
    memo = fields.Char(string="Memo")
    is_cashed = fields.Boolean(string="Cashed")
    comments = fields.Text(string="Comments")
    bank_label = fields.Char(string="Bank label")
