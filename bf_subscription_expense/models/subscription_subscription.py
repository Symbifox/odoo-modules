from odoo import _, api, fields, models

# Une dépense compte dès qu'elle est décidée : approuvée (pièce en brouillon ou
# comptabilisée, pas encore remboursée) ou payée.
COUNTED_STATES = ("approved", "done")


class SubscriptionSubscription(models.Model):
    _inherit = "subscription.subscription"

    expense_ids = fields.One2many(
        "hr.expense", "subscription_id", string="Expenses",
    )
    expense_count = fields.Integer(compute="_compute_expense_count", string="# Expenses")

    # En sudo : ces totaux sont calculés avec les droits de qui regarde, et les
    # règles de hr.expense ne montrent à un employé que ses propres dépenses. Sans
    # sudo, un gestionnaire des abonnements sans droits sur les notes de frais lisait
    # 0 $ là où le récapitulatif (en sudo) disait le vrai montant.
    def _counted_expenses(self):
        self.ensure_one()
        return self.sudo().expense_ids.filtered(lambda e: e.state in COUNTED_STATES)

    @api.depends("expense_ids")
    def _compute_expense_count(self):
        for rec in self:
            rec.expense_count = len(rec.sudo().expense_ids)

    @api.depends(
        "vendor_bill_ids.amount_total_signed",
        "vendor_bill_ids.state",
        "customer_invoice_ids.amount_total_signed",
        "customer_invoice_ids.state",
        "expense_ids.total_amount",
        "expense_ids.state",
    )
    def _compute_totals(self):
        super()._compute_totals()
        for rec in self:
            # total_amount : devise de la société, taxes comprises, comme le
            # amount_total_signed des factures.
            rec.total_paid_lifetime += sum(rec._counted_expenses().mapped("total_amount"))

    @api.depends(
        "vendor_bill_ids.invoice_date",
        "vendor_bill_ids.state",
        "expense_ids.date",
        "expense_ids.state",
    )
    def _compute_last_vendor_bill_date(self):
        super()._compute_last_vendor_bill_date()
        for rec in self:
            dates = [d for d in rec._counted_expenses().mapped("date") if d]
            if rec.last_vendor_bill_date:
                dates.append(rec.last_vendor_bill_date)
            rec.last_vendor_bill_date = max(dates) if dates else False

    def action_view_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Expenses"),
            "res_model": "hr.expense",
            "view_mode": "list,form",
            "domain": [("subscription_id", "=", self.id)],
            "context": {"default_subscription_id": self.id},
        }
