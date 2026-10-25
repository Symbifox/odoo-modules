from odoo import fields, models


class HrExpense(models.Model):
    _inherit = "hr.expense"

    subscription_id = fields.Many2one(
        "subscription.subscription",
        string="Subscription",
        index=True, copy=False, ondelete="set null", tracking=True, check_company=True,
        domain="[('company_id', '=', company_id)]",
        help="Subscription this expense pays. An approved or paid expense counts "
             "in the subscription's total paid and its last bill date.",
    )
