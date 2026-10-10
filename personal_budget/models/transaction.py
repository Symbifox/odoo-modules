from odoo import api, fields, models


class PersonalBudgetTransaction(models.Model):
    _name = 'personal.budget.transaction'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Budget transaction'
    _order = 'date desc, id desc'

    # Par budget : un compte conjoint importé par les deux personnes du ménage,
    # chacune dans son budget, ne doit ni se heurter ni révéler l'autre import.
    _sql_constraints = [
        ('account_fitid_uniq', 'unique(book_id, account_ref, fitid)',
         "This OFX transaction (account + FITID) already exists in this budget."),
    ]

    # Le budget d'une transaction est celui de sa catégorie : elle ne peut pas
    # le contredire.
    book_id = fields.Many2one(
        related='category_id.book_id', store=True, readonly=True,
        default=None, required=False,
    )
    _book_moving_fields = ('book_id', 'category_id')
    _book_target_fields = ('contributor_id', 'recurring_id')

    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    category_id = fields.Many2one(
        'personal.budget.category', string="Category", required=True,
        ondelete='restrict',
    )
    category_type = fields.Selection(
        related='category_id.category_type', store=True, string="Type",
    )
    gross_amount = fields.Float(string="Gross amount", digits=(12, 2), required=True)
    details = fields.Char(string="Details")
    account_ref = fields.Char(string="Account (ref.)", index=True, copy=False)
    fitid = fields.Char(
        string="FITID", index=True, copy=False,
        help="Unique transaction identifier in the OFX statement.",
    )
    self_percent = fields.Float(string="% me", default=100.0)
    contributor_id = fields.Many2one(
        'personal.budget.contributor', string="Contributor",
        ondelete='set null',
    )
    contributor_percent = fields.Float(
        string="% contributor", compute='_compute_shares', store=True,
    )
    self_amount = fields.Float(
        string="My amount", digits=(12, 2),
        compute='_compute_shares', store=True,
    )
    year = fields.Integer(
        string="Year", compute='_compute_period', store=True,
    )
    month = fields.Integer(
        string="Month", compute='_compute_period', store=True,
    )
    recurring_id = fields.Many2one(
        'personal.budget.recurring', string="Recurring expense",
        ondelete='set null', index=True, readonly=True, copy=False,
        help="Recurring expense payment that this transaction settles.",
    )
    month_number = fields.Integer(
        string="# month (absolute)", compute='_compute_period', store=True,
        help="Absolute month since January 2013 (for sorting/pivot)",
    )

    @api.constrains('contributor_id', 'recurring_id', 'category_id')
    def _check_contributor_book(self):
        self._check_same_book('contributor_id')
        # `readonly` ne protège rien par RPC : une dépense récurrente d'un autre
        # budget rendait son nom à travers celui de la transaction.
        self._check_same_book('recurring_id')

    @api.depends('self_percent', 'gross_amount')
    def _compute_shares(self):
        for rec in self:
            rec.contributor_percent = 100.0 - (rec.self_percent or 0.0)
            rec.self_amount = (rec.gross_amount or 0.0) * (rec.self_percent or 0.0) / 100.0

    @api.depends('date')
    def _compute_period(self):
        for rec in self:
            if rec.date:
                rec.year = rec.date.year
                rec.month = rec.date.month
                rec.month_number = (rec.date.year - 2013) * 12 + rec.date.month
            else:
                rec.year = 0
                rec.month = 0
                rec.month_number = 0
