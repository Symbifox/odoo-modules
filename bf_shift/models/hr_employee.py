from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tools import flag

MANAGER = "bf_shift.group_shift_manager"
# Own prefetch group: reading an employee's name must not pull these fields.
# A shift manager who is not an HR officer reads hr.employee through its
# public profile, which refuses any private stored field in the batch.
PREFETCH = "bf_shift"


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    shift_agreement_id = fields.Many2one(
        "bf.shift.agreement", string="Working conditions", groups=MANAGER, prefetch=PREFETCH,
        help="Labour standards or collective agreement that applies to this person's shifts.")
    shift_seniority_date = fields.Date("Seniority date", groups=MANAGER, prefetch=PREFETCH)
    shift_hourly_rate = fields.Monetary(
        "Usual hourly rate", currency_field="shift_currency_id", groups=MANAGER, prefetch=PREFETCH,
        help="Used to compute premiums and amounts. Without it, the export carries hours only.")
    shift_currency_id = fields.Many2one(related="company_id.currency_id", groups=MANAGER, prefetch=PREFETCH,
                                        string="Shift currency")
    shift_pay_ref = fields.Char("Payroll number", groups=MANAGER, prefetch=PREFETCH)
    shift_flexible_hours = fields.Boolean(
        "Variable or non-continuous hours", groups=MANAGER, prefetch=PREFETCH,
        help="Daily hours that vary, or that are worked in several pieces (split shifts): the "
        "only daily refusal limit is 12 hours per 24 hours, instead of 2 hours beyond the usual "
        "day and 14 hours per 24 hours (LNT art. 59.0.1). A day with a split shift is treated "
        "this way even when the box is not ticked. A working calendar with flexible hours "
        "counts as ticked.")
    shift_availability_required = fields.Boolean(
        "Duties require availability", groups=MANAGER, prefetch=PREFETCH,
        help="The 5-day notice rule does not apply to this person (LNT art. 59.0.1).")
    shift_bank_balance = fields.Float("Time bank (h)", compute="_compute_shift_bank_balance",
                                      groups=MANAGER, prefetch=PREFETCH)
    shift_bank_carried = fields.Float(
        "Time bank carried over (h)", groups=MANAGER, prefetch=PREFETCH, readonly=True,
        help="Net of the time bank lines of the pay periods destroyed at the end of their "
        "retention period: the balance does not move when they go.")
    shift_retention_hold = fields.Boolean(
        "Keep for a dispute", groups=MANAGER, prefetch=PREFETCH, tracking=True,
        help="A grievance, a complaint or a tax objection concerns this person: none of their "
        "schedules, pay periods, benefits or availability is destroyed at the end of the "
        "retention period, nor any schedule or pay period they appear in, until the box is "
        "unticked.")
    shift_retention_hold_note = fields.Char("Dispute", groups=MANAGER, prefetch=PREFETCH,
                                            tracking=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if not flag(self.env, "retention"):
            res.pop("shift_bank_carried", None)   # the context could carry one
        return res

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("shift_bank_carried") for vals in vals_list) and \
                not flag(self.env, "retention"):
            raise UserError(_("The time bank carried over is set by the destruction of old "
                              "pay periods only."))
        return super().create(vals_list)

    def write(self, vals):
        # Readonly in the form only: over RPC, a manager with HR rights could
        # move a time bank balance without a trace. Only the destruction of
        # old pay periods carries their net here.
        if "shift_bank_carried" in vals and not flag(self.env, "retention"):
            raise UserError(_("The time bank carried over is set by the destruction of old "
                              "pay periods only."))
        return super().write(vals)

    def _compute_shift_bank_balance(self):
        lines = self.env["bf.shift.pay.line"].sudo().search([
            ("employee_id", "in", self.ids),
            ("period_id.state", "=", "exported"),
            ("code", "in", ("BANKIN", "BANKOUT")),
        ])
        balance = {emp.id: emp.sudo().shift_bank_carried for emp in self}
        for line in lines:
            sign = 1.0 if line.code == "BANKIN" else -1.0
            balance[line.employee_id.id] = balance.get(line.employee_id.id, 0.0) + sign * line.hours
        for emp in self:
            emp.shift_bank_balance = balance.get(emp.id, 0.0)

    def _shift_calendar(self):
        self.ensure_one()
        return self.sudo().resource_calendar_id or self.sudo().company_id.resource_calendar_id

    def _shift_usual_hours(self):
        """Usual hours of a day, as a function of the day: the hours of that
        weekday in the person's working calendar (LNT art. 59.0.1 reads the
        usual day weekday by weekday: 8 h on Monday, 7 h on Tuesday, 10 h on
        Friday is a regular schedule). A weekday the calendar does not work
        falls back on its average day, and without a calendar on 8 hours."""
        self.ensure_one()
        cal = self._shift_calendar()
        if not cal:
            return lambda day: 8.0
        average = cal.hours_per_day or 8.0
        lines = cal.attendance_ids.filtered(
            lambda a: not a.display_type and a.day_period != "lunch" and not a.resource_id)
        Attendance = self.env["resource.calendar.attendance"]
        two_weeks = cal.two_weeks_calendar

        def hours(day):
            week_type = str(Attendance.get_week_type(day)) if two_weeks else None
            total = sum(
                a.hour_to - a.hour_from for a in lines
                if int(a.dayofweek) == day.weekday()
                and (not a.date_from or a.date_from <= day)
                and (not a.date_to or a.date_to >= day)
                and (week_type is None or a.week_type == week_type))
            return total or average
        return hours

    def _shift_variable_hours(self):
        """Variable or non-continuous hours (art. 59.0.1): ticked on the
        employee, or a working calendar with flexible hours."""
        self.ensure_one()
        cal = self._shift_calendar()
        return bool(self.sudo().shift_flexible_hours or (cal and cal.flexible_hours))
