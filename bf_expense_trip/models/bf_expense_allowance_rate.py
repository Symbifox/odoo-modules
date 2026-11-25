# -*- coding: utf-8 -*-
"""The company's allowance rates: an amount per meal, incidentals and private lodging.

⚠️ The rates are the employer's policy, never written in the code: whether an allowance is
reasonable (and so not taxable, ARC « Travel expenses », Revenu Québec « Allocation pour frais
de voyage ») depends on the employer and the place. The federal rates are offered as a
starting point the office loads, dates and can change.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

KINDS = [
    ("breakfast", "Breakfast"),
    ("lunch", "Lunch"),
    ("dinner", "Dinner"),
    ("incidentals", "Incidentals"),
    ("private_lodging", "Private lodging"),
]

# National Joint Council, Travel Directive, Appendix C, Canada column, taxes included,
# effective October 1, 2026 (read at njc-cnm.gc.ca on 2026-11-24).
NJC_2026_10 = {
    "breakfast": 29.95,
    "lunch": 30.55,
    "dinner": 62.70,
    "incidentals": 25.00,
    "private_lodging": 50.00,
}
NJC_2026_10_DATE = "2026-10-01"


class BfExpenseAllowanceRate(models.Model):
    _name = "bf.expense.allowance.rate"
    _description = "Allowance rate"
    _order = "company_id, kind, date_from desc"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    kind = fields.Selection(KINDS, required=True)
    date_from = fields.Date(string="From", required=True,
                            help="The rate applies to the days from this date, until a later rate.")
    amount = fields.Monetary(currency_field="currency_id", required=True)
    product_id = fields.Many2one(
        "product.product", string="Expense category", required=True, check_company=True,
        domain=[("can_be_expensed", "=", True)],
        default=lambda self: self.env.ref(
            "bf_expense_trip.product_travel_allowance", raise_if_not_found=False),
        help="Category of the expenses this rate creates. It must not carry a cost of its own.")
    source = fields.Char(help="Where the rate comes from: a policy, a directive, a date.")

    _sql_constraints = [
        ("bf_rate_unique", "unique(company_id, kind, date_from)",
         "A company has one rate per allowance and start date."),
    ]

    @api.constrains("amount", "product_id")
    def _check_amount(self):
        for rate in self:
            if rate.amount < 0:
                raise ValidationError(_("An allowance rate cannot be negative."))
            if rate.product_id.with_company(rate.company_id).standard_price:
                raise ValidationError(_(
                    "The \"%s\" expense category has a cost of its own, which would replace "
                    "the allowance: choose a category without a cost.",
                    rate.product_id.display_name))

    @api.model
    def _bf_rate_for(self, company, kind, day):
        """The rate in force on ``day``: the latest one starting on or before it."""
        return self.search([
            ("company_id", "=", company.id),
            ("kind", "=", kind),
            ("date_from", "<=", day),
        ], order="date_from desc", limit=1)

    @api.model
    def action_load_njc_rates(self):
        """Load the federal rates of October 1, 2026 in the current company, once."""
        company = self.env.company
        if company.currency_id != self.env.ref("base.CAD"):
            raise UserError(_(
                "The federal rates are in Canadian dollars, and %(company)s keeps its books in "
                "%(currency)s: enter the company's own rates.",
                company=company.name, currency=company.currency_id.name))
        product = self.env.ref("bf_expense_trip.product_travel_allowance")
        existing = set(self.search([
            ("company_id", "=", company.id), ("date_from", "=", NJC_2026_10_DATE),
        ]).mapped("kind"))
        self.create([{
            "company_id": company.id,
            "kind": kind,
            "date_from": NJC_2026_10_DATE,
            "amount": amount,
            "product_id": product.id,
            "source": _("National Joint Council, Travel Directive, Appendix C (Canada), "
                        "October 1, 2026"),
        } for kind, amount in NJC_2026_10.items() if kind not in existing])
        return {"type": "ir.actions.client", "tag": "reload"}
