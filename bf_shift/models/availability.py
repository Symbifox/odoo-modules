from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .tools import check_own, guard_employee_vals, local_bounds, to_local, tz_of

EMPLOYEE_FIELDS = {"employee_id", "kind", "recurrence", "weekday_id", "date_from", "date_to",
                   "whole_day", "hour_from", "hour_to", "note"}


class BfShiftAvailability(models.Model):
    _name = "bf.shift.availability"
    _description = "Availability"
    _order = "employee_id, date_from, weekday_id"

    employee_id = fields.Many2one("hr.employee", required=True, index=True,
                                  default=lambda self: self.env.user.employee_id)
    employee_user_id = fields.Many2one("res.users", compute="_compute_user", store=True,
                                       compute_sudo=True)
    company_id = fields.Many2one(related="employee_id.company_id", store=True)
    kind = fields.Selection([("unavailable", "Unavailable"), ("available", "Available")],
                            default="unavailable", required=True)
    recurrence = fields.Selection([("weekly", "Every week"), ("dates", "Dates")],
                                  default="weekly", required=True)
    weekday_id = fields.Many2one("bf.shift.weekday", string="Day")
    date_from = fields.Date("From", help="Weekly: first week it applies. Dates: first day.")
    date_to = fields.Date("To", help="Empty: no end (weekly only).")
    whole_day = fields.Boolean(default=True)
    hour_from = fields.Float("From (hour)")
    hour_to = fields.Float("To (hour)")
    note = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        records = super().create(vals_list)
        check_own(records)
        return records

    def write(self, vals):
        guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        res = super().write(vals)
        check_own(self)
        return res

    @api.depends("employee_id.user_id")
    def _compute_user(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id

    @api.constrains("recurrence", "weekday_id", "date_from", "date_to", "whole_day",
                    "hour_from", "hour_to")
    def _check(self):
        for rec in self:
            if rec.recurrence == "weekly" and not rec.weekday_id:
                raise ValidationError(_("Choose the day of the week."))
            if rec.recurrence == "dates" and not (rec.date_from and rec.date_to):
                raise ValidationError(_("Give the first and the last day."))
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("The last day comes after the first."))
            if not rec.whole_day and rec.hour_from == rec.hour_to:
                raise ValidationError(_("Give the hours, or tick the whole day."))

    def _days(self, first, last):
        self.ensure_one()
        start = max(first, self.date_from) if self.date_from else first
        end = min(last, self.date_to) if self.date_to else last
        day = start
        while day <= end:
            if self.recurrence == "dates" or day.weekday() == self.weekday_id.code:
                yield day
            day += timedelta(days=1)

    def _intervals(self, first, last):
        for rec in self:
            for day in rec._days(first, last):
                if rec.whole_day:
                    yield (datetime.combine(day, time(0, 0)),
                           datetime.combine(day + timedelta(days=1), time(0, 0)))
                else:
                    yield local_bounds(day, rec.hour_from, rec.hour_to)

    @api.model
    def _unavailable_intervals(self, employee, first, last):
        """Local intervals where ``employee`` said they cannot work."""
        recs = self.search([("employee_id", "=", employee.id), ("kind", "=", "unavailable")])
        return list(recs._intervals(first, last))

    @api.model
    def _is_unavailable(self, employee, start_utc, end_utc):
        tz = tz_of(employee, self.env)
        start = to_local(start_utc, tz)
        end = to_local(end_utc, tz)
        for u0, u1 in self._unavailable_intervals(employee, start.date() - timedelta(days=1),
                                                  end.date()):
            if u0 < end and start < u1:
                return True
        return False
