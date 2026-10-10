from odoo import api, fields, models


class PersonalBudgetInvoice(models.Model):
    _name = 'personal.budget.invoice'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Freelance invoice'
    _order = 'invoice_date desc, id desc'

    invoice_number = fields.Char(string="Number", required=True)
    partner_name = fields.Char(string="Customer")
    invoice_date = fields.Date(string="Invoice date", required=True)
    due_date = fields.Date(string="Due date")
    pretax_amount = fields.Float(
        string="Pre-tax amount", digits=(12, 2), required=True,
    )
    description = fields.Char(string="Description")
    tax_reserve = fields.Float(
        string="Tax reserve (40%)", digits=(12, 2),
        compute='_compute_tax_split', store=True,
    )
    operating_amount = fields.Float(
        string="Operating amount (60%)", digits=(12, 2),
        compute='_compute_tax_split', store=True,
    )
    company_label = fields.Char(string="Company")
    # Un booléen jamais écrit vaut NULL en base, pas FALSE : le résumé du
    # tableau de bord filtre donc sur `IS NOT TRUE`.
    is_duplicate = fields.Boolean(string="Copy", default=False)

    @api.depends('pretax_amount')
    def _compute_tax_split(self):
        for rec in self:
            rec.tax_reserve = (rec.pretax_amount or 0.0) * 0.40
            rec.operating_amount = (rec.pretax_amount or 0.0) * 0.60
