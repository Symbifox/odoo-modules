from odoo import api, fields, models


class PersonalBudgetLoan(models.Model):
    _name = 'personal.budget.loan'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Loan'
    _order = 'start_date desc'

    name = fields.Char(string="Name", required=True)
    initial_amount = fields.Float(
        string="Initial amount", digits=(12, 2), required=True,
    )
    total_months = fields.Integer(string="Term (months)")
    monthly_payment = fields.Float(
        string="Monthly payment", digits=(12, 2),
    )
    start_date = fields.Date(string="Start date")
    notes = fields.Text(string="Notes")
    line_ids = fields.One2many(
        'personal.budget.loan.line', 'loan_id', string="Lines",
    )
    current_balance = fields.Float(
        string="Current balance", digits=(12, 2),
        compute='_compute_current_balance', store=True,
    )
    is_overdue = fields.Boolean(
        string="Overdue", compute='_compute_is_overdue',
    )

    @api.depends('initial_amount', 'line_ids.amount')
    def _compute_current_balance(self):
        for loan in self:
            loan.current_balance = loan.initial_amount + sum(
                loan.line_ids.mapped('amount')
            )

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for loan in self:
            if loan.start_date and loan.total_months and loan.monthly_payment:
                expected_payments = 0
                dt = loan.start_date
                while dt <= today and expected_payments < loan.total_months:
                    expected_payments += 1
                    dt = dt.replace(
                        year=dt.year + (dt.month // 12),
                        month=(dt.month % 12) + 1,
                        day=min(dt.day, 28),
                    )
                actual_paid = abs(sum(
                    line.amount for line in loan.line_ids if line.amount < 0
                ))
                expected_paid = expected_payments * loan.monthly_payment
                loan.is_overdue = actual_paid < expected_paid - 0.01
            else:
                loan.is_overdue = False


class PersonalBudgetLoanLine(models.Model):
    _name = 'personal.budget.loan.line'
    _inherit = ['personal.budget.book.mixin']
    _description = 'Loan line'
    _order = 'date, id'

    # Le budget d'une ligne est celui de son prêt.
    book_id = fields.Many2one(
        related='loan_id.book_id', store=True, readonly=True,
        default=None, required=False,
    )
    _book_moving_fields = ('book_id', 'loan_id')

    loan_id = fields.Many2one(
        'personal.budget.loan', string="Loan", required=True,
        ondelete='cascade',
    )
    date = fields.Date(string="Date")
    description = fields.Char(string="Description")
    amount = fields.Float(
        string="Amount", digits=(12, 2), required=True,
        help="Positive = fees/interest, Negative = payment",
    )
    running_balance = fields.Float(
        string="Running balance", digits=(12, 2), readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        loans = records.mapped('loan_id')
        for loan in loans:
            self._recompute_running_balance(loan)
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'amount' in vals or 'date' in vals:
            loans = self.mapped('loan_id')
            for loan in loans:
                self._recompute_running_balance(loan)
        return res

    def unlink(self):
        loans = self.mapped('loan_id')
        res = super().unlink()
        for loan in loans:
            self._recompute_running_balance(loan)
        return res

    @api.model
    def _recompute_running_balance(self, loan):
        lines = self.sudo().search(
            [('loan_id', '=', loan.id)], order='date, id',
        )
        balance = loan.initial_amount
        for line in lines:
            balance += line.amount
            if line.running_balance != balance:
                line.with_context(skip_recompute=True).sudo().write(
                    {'running_balance': balance}
                )
