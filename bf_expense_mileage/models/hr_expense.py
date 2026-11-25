# -*- coding: utf-8 -*-
"""Where a claim of kilometres falls between the full and the reduced rate.

⚠️ The count is the employee's whole calendar year, whoever typed it and whatever its date:
the expenses at either rate, refused ones and negative drafts excepted. Counting only the days
before the claim let a claim dated in March, typed after June's 5,000 km, start over at the
full rate: the yearly cap is on the total, not on the order of the dates.
⚠️ The products of Work routes' mileage (``bf_route_expense``) are counted too, whichever of the
two modules was installed first: until that module reads these settings, it may hold its own
reduced product, and the year must stay one count. It is read in sudo
because the person who claims (an assistant, a manager) may not see every expense of the
employee; what comes out is two numbers of kilometres, never the expenses themselves.
"""
from datetime import date

from odoo import api, models


class HrExpense(models.Model):
    _inherit = "hr.expense"

    @api.model
    def _bf_mileage_products(self, company):
        company = company.sudo()
        full = company.bf_mileage_product_id or self.env.ref(
            "hr_expense.expense_product_mileage", raise_if_not_found=False)
        reduced = company.bf_mileage_reduced_product_id or self.env.ref(
            "bf_expense_mileage.product_mileage_reduced", raise_if_not_found=False)
        return full, reduced

    @api.model
    def _bf_mileage_earlier_domain(self, employee, day, products):
        return [
            ("employee_id", "=", employee.id),
            ("product_id", "in", products.ids),
            ("date", ">=", date(day.year, 1, 1)),
            ("date", "<=", date(day.year, 12, 31)),
            ("state", "!=", "refused"),
            # A negative DRAFT typed by hand would keep every kilometre at the full rate; an
            # approved correction (a negative expense past the draft) does count.
            "|", ("quantity", ">", 0), ("state", "not in", ("draft", "reported")),
        ]

    @api.model
    def _bf_mileage_counted(self, employee, day, company):
        """Kilometres already claimed by the employee in the calendar year of ``day``."""
        full, reduced = self._bf_mileage_products(company)
        products = full | reduced
        sudo_company = company.sudo()
        for name in ("bf_route_mileage_product_id", "bf_route_mileage_reduced_product_id"):
            if name in sudo_company._fields:
                products |= sudo_company[name]
        for xmlid in ("bf_route_expense.product_mileage_reduced",
                      "hr_expense.expense_product_mileage"):
            # Odoo's own Mileage category counts even when the company chose another one.
            product = self.env.ref(xmlid, raise_if_not_found=False)
            if product:
                products |= product
        earlier = self.sudo().search(self._bf_mileage_earlier_domain(employee, day, products))
        return sum(earlier.mapped("quantity"))

    @api.model
    def _bf_mileage_lines(self, employee, day, km, company):
        """[(product, km)]: the kilometres at the full rate, then those beyond the threshold."""
        km = round(km, 1)
        full, reduced = self._bf_mileage_products(company)
        threshold = company.sudo().bf_mileage_threshold
        if km <= 0:
            return []
        if threshold <= 0 or not reduced:
            return [(full, km)]
        left = max(0.0, threshold - self._bf_mileage_counted(employee, day, company))
        at_full = round(min(km, left), 1)
        lines = []
        if at_full > 0:
            lines.append((full, at_full))
        if round(km - at_full, 1) > 0:
            lines.append((reduced, round(km - at_full, 1)))
        return lines
