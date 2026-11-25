# -*- coding: utf-8 -*-
"""Add mileage: one claim of kilometres, split between the two rates of the year."""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import formatLang


class BfExpenseMileageWizard(models.TransientModel):
    _name = "bf.expense.mileage.wizard"
    _description = "Add mileage"

    def _default_employee_id(self):
        return self.env.user.employee_id

    employee_id = fields.Many2one(
        "hr.employee", string="Employee", required=True, default=_default_employee_id,
        domain=[("filter_for_expense", "=", True)])
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company)
    date = fields.Date(required=True, default=fields.Date.context_today)
    name = fields.Char(string="Description", required=True,
                       help="Where from, where to, and why: what the employer must be able to "
                            "check in the log.")
    distance_km = fields.Float(string="Kilometres", digits=(16, 1), required=True)
    preview = fields.Html(compute="_compute_preview", sanitize=False)

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id.company_id:
            self.company_id = self.employee_id.company_id

    def _bf_may_claim_for(self, employee):
        """The employees the user may choose on an expense (oneself, one's team, the people
        one assists), read as the user. ⚠️ The field's domain is applied by the browser only:
        without this check, any employee's count of the year could be read through the preview."""
        if self.company_id and self.company_id not in self.env.user.company_ids:
            return False
        return bool(employee) and bool(self.env["hr.employee"].search_count(
            [("filter_for_expense", "=", True), ("id", "=", employee.id)], limit=1))

    @api.depends("employee_id", "company_id", "date", "distance_km")
    def _compute_preview(self):
        Expense = self.env["hr.expense"]
        for wizard in self:
            if not (wizard.employee_id and wizard.date and wizard.distance_km > 0
                    and wizard._bf_may_claim_for(wizard.employee_id)):
                wizard.preview = False
                continue
            rows = []
            for product, km in Expense._bf_mileage_lines(
                    wizard.employee_id, wizard.date, wizard.distance_km, wizard.company_id):
                rate = product.with_company(wizard.company_id).standard_price
                rows.append(Markup("<li>%s</li>") % _(
                    "%(km)s km at %(rate)s (%(product)s)",
                    km=formatLang(self.env, km, digits=1), rate=formatLang(self.env, rate, digits=2),
                    product=product.display_name))
            wizard.preview = Markup("<ul class='mb-0'>%s</ul>") % Markup().join(rows)

    def _bf_mileage_expense_vals(self, product, km):
        """Values of one expense; a hook for the modules that attach it somewhere (a trip)."""
        self.ensure_one()
        return {
            "name": self.name,
            "employee_id": self.employee_id.id,
            "company_id": self.company_id.id,
            "product_id": product.id,
            "product_uom_id": product.uom_id.id,
            "quantity": km,
            "date": self.date,
        }

    def action_create(self):
        self.ensure_one()
        if self.distance_km <= 0:
            raise UserError(_("Enter the kilometres driven."))
        if not self._bf_may_claim_for(self.employee_id):
            # ⚠️ Before reading any rate: a « no rate » message, or the split itself, would
            # tell someone else's count of the year.
            raise AccessError(_("You cannot claim mileage for %s.", self.employee_id.name))
        Expense = self.env["hr.expense"]
        lines = Expense._bf_mileage_lines(
            self.employee_id, self.date, self.distance_km, self.company_id)
        for product, _km in lines:
            if not product.with_company(self.company_id).standard_price > 0:
                raise UserError(_(
                    "The \"%s\" expense category has no rate in this company: set it in the "
                    "Expenses settings.", product.display_name))
        # Created as the person who claims: the access rules of Expenses decide whether they
        # may claim for this employee (an AccessError otherwise, proved by a test).
        expenses = Expense.with_context(default_employee_id=self.employee_id.id).create(
            [self._bf_mileage_expense_vals(product, km) for product, km in lines])
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense",
            "name": _("Mileage"),
            "view_mode": "list,form",
            "domain": [("id", "in", expenses.ids)],
            "target": "current",
        }
