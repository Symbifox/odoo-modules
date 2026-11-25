# -*- coding: utf-8 -*-
"""Whoever prepares an employee's expenses does not approve them.

⚠️ Odoo already refuses an approver their own report. An assistant submits the traveller's
report, so without these guards an assistant who is also an expense approver (an office
manager, very often) would approve what they prepared themselves. Administrators of Expenses
keep the right, as Odoo gives it to them for their own reports.

Odoo checks ``can_approve`` in three places only (``action_approve_expense_sheets``, the refusal,
and ``write`` of a state). Three other paths needed closing:
- ``create`` takes ``approval_state`` as given: a report created already approved;
- ``user_id`` (the manager of the report) set to oneself opens Odoo's approver rule on it;
- ``can_reset`` is true for any team approver, and the reset reverses the journal entries in
  sudo: an approved or posted report of the traveller pulled back to draft.
Two more: a plain ``write`` of ``approval_state`` (False, draft) un-approves
without the reset, since Odoo's guard only fires towards « approve » or « cancel »; and a write
that moves a colleague's sheet to the boss AND approves it was judged on the colleague. Hence:
an assistant may only write ``submit`` as a state, and the values are judged on the employee
the sheet ends up with. Odoo's own reset and refusal write in sudo and are not affected.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

NOT_YET_APPROVED = {None, False, "draft", "submit"}


class HrExpenseSheet(models.Model):
    _inherit = "hr.expense.sheet"

    trip_id = fields.Many2one(
        "bf.expense.trip", string="Trip", index="btree_not_null", ondelete="set null",
        check_company=True, readonly=True)

    def _bf_trip_is_assistant_of(self, employee):
        """The current user prepares this employee's expenses and does not administer Expenses."""
        if self.env.su or self.env.user.has_group("hr_expense.group_hr_expense_manager"):
            return False
        return self.env.user in employee.sudo().bf_trip_assistant_user_ids

    def _bf_trip_check_assistant_vals(self, vals, employee):
        if not self._bf_trip_is_assistant_of(employee):
            return
        for key in ("approval_state", "state"):
            if key in vals and vals[key] not in ("submit", None) and not (
                    vals[key] in (False, "draft") and vals.get("_bf_from_draft")):
                raise UserError(_("You prepare %s's expenses: you cannot approve them.",
                                  employee.name))
        if (vals.get("approval_state") not in NOT_YET_APPROVED
                or vals.get("state") not in NOT_YET_APPROVED):
            raise UserError(_("You prepare %s's expenses: you cannot approve them.",
                              employee.name))
        if vals.get("user_id") == self.env.user.id:
            raise UserError(_(
                "You prepare %s's expenses: you cannot be the manager of their report.",
                employee.name))

    @api.model_create_multi
    def create(self, vals_list):
        Employee = self.env["hr.employee"]
        for vals in vals_list:
            employee = Employee.browse(vals.get("employee_id") or self._default_employee_id().id)
            # A new report may start in draft (Odoo passes « state: draft »), never approved.
            self._bf_trip_check_assistant_vals(dict(vals, _bf_from_draft=True), employee)
        return super().create(vals_list)

    def write(self, vals):
        if {"user_id", "employee_id", "approval_state", "state"} & set(vals):
            for sheet in self:
                # Judged on the employee the sheet ends up with, and on the one it had.
                employees = sheet.employee_id
                if vals.get("employee_id"):
                    employees |= self.env["hr.employee"].browse(vals["employee_id"])
                checked = dict(vals, user_id=vals.get("user_id") or (
                    sheet.user_id.id if vals.get("employee_id") else None),
                    _bf_from_draft=sheet.state in ("draft", "submit"))
                for employee in employees:
                    self._bf_trip_check_assistant_vals(checked, employee)
        return super().write(vals)

    @api.constrains("trip_id", "employee_id", "company_id")
    def _check_trip(self):
        for sheet in self.filtered("trip_id"):
            trip = sheet.trip_id.sudo()
            if trip.employee_id != sheet.employee_id or trip.company_id != sheet.company_id:
                raise ValidationError(_(
                    "The report \"%(report)s\" and the trip \"%(trip)s\" are not the same "
                    "employee's.", report=sheet.name, trip=trip.name))

    @api.depends_context("uid")
    def _compute_can_approve(self):
        super()._compute_can_approve()
        for sheet in self:
            if self._bf_trip_is_assistant_of(sheet.employee_id):
                sheet.can_approve = False
                sheet.cannot_approve_reason = _(
                    "%s: you prepare this employee's expenses", sheet.name)

    def _compute_is_editable(self):
        """The assistant edits the traveller's draft report, as the traveller would (Odoo
        lets only the owner edit a draft sheet, so the folio split inside it was refused)."""
        super()._compute_is_editable()
        for sheet in self:
            if sheet.state == "draft" and not sheet.is_editable and (
                    self.env.user in sheet.employee_id.sudo().bf_trip_assistant_user_ids):
                sheet.is_editable = True

    @api.depends_context("uid")
    def _compute_can_reset(self):
        super()._compute_can_reset()
        for sheet in self:
            # Back to draft from Submitted or Refused stays possible (to correct a receipt);
            # an approved, posted or paid report is not the assistant's to undo.
            if (sheet.state not in ("draft", "submit", "cancel")
                    and self._bf_trip_is_assistant_of(sheet.employee_id)):
                sheet.can_reset = False
