# -*- coding: utf-8 -*-
"""Travel assistants: the users who prepare an employee's trips and expenses.

⚠️ Odoo lets a user choose the employee of an expense through ``filter_for_expense``: oneself,
or one's team for an approver. An assistant is neither, so the search grows by the employees
who name this user as their assistant. The ids are read in sudo on ``hr.employee`` because the
same search runs on ``hr.employee.public``, which does not carry the field.
"""
from odoo import fields, models
from odoo.osv import expression


class HrEmployeeBase(models.AbstractModel):
    _inherit = "hr.employee.base"

    def _search_filter_for_expense(self, operator, value):
        domain = super()._search_filter_for_expense(operator, value)
        assisted = self.env["hr.employee"].sudo().search(
            [("bf_trip_assistant_user_ids", "in", self.env.user.id)]).ids
        if not assisted:
            return domain
        return expression.OR([domain, [("id", "in", assisted)]])


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    bf_trip_assistant_user_ids = fields.Many2many(
        "res.users", "bf_expense_trip_assistant_rel", "employee_id", "user_id",
        string="Travel assistants", groups="hr.group_hr_user",
        domain=[("share", "=", False)],
        help="Users who prepare this employee's trips and expenses: they can create, attach, "
             "split and submit them, never approve them.")
