# -*- coding: utf-8 -*-
from odoo import fields, models


class BfExpenseMileageWizard(models.TransientModel):
    _inherit = "bf.expense.mileage.wizard"

    bf_trip_id = fields.Many2one(
        "bf.expense.trip", string="Trip",
        domain="[('employee_id', '=', employee_id), ('state', '=', 'draft')]")

    def _bf_mileage_expense_vals(self, product, km):
        vals = super()._bf_mileage_expense_vals(product, km)
        if self.bf_trip_id:
            vals["trip_id"] = self.bf_trip_id.id
        return vals
