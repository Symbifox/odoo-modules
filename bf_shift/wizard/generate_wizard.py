from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.tools import local_bounds, to_utc, tz_of


class BfShiftGenerateWizard(models.TransientModel):
    _name = "bf.shift.generate.wizard"
    _description = "Add shifts from a template"

    schedule_id = fields.Many2one("bf.shift.schedule", required=True, ondelete="cascade")
    template_id = fields.Many2one("bf.shift.template", required=True)
    employee_ids = fields.Many2many("hr.employee", string="Employees",
                                    help="Empty: open shifts, to be offered.")
    open_count = fields.Integer("Open shifts per day", default=1)
    date_from = fields.Date("From", required=True)
    date_to = fields.Date("To", required=True)
    weekday_ids = fields.Many2many("bf.shift.weekday", string="Days",
                                   help="Empty: every day.")

    def action_generate(self):
        self.ensure_one()
        sched = self.schedule_id
        if self.date_from < sched.date_from or self.date_to > sched.date_to:
            raise UserError(_("The dates must fall within the schedule."))
        tpl = self.template_id
        codes = set(self.weekday_ids.mapped("code"))
        people = list(self.employee_ids) or [False] * max(1, self.open_count)
        vals = []
        day = self.date_from
        while day <= self.date_to:
            if not codes or day.weekday() in codes:
                for emp in people:
                    tz = tz_of(emp or None, self.env)
                    start, end = local_bounds(day, tpl.hour_from, tpl.hour_to)
                    vals.append({
                        "schedule_id": sched.id,
                        "employee_id": emp.id if emp else False,
                        "template_id": tpl.id,
                        "job_id": tpl.job_id.id,
                        "kind": "on_call" if tpl.kind == "on_call" else "work",
                        "start": to_utc(start, tz),
                        "end": to_utc(end, tz),
                        "break_minutes": tpl.break_minutes,
                        "break_paid": tpl.break_paid,
                    })
            day += timedelta(days=1)
        self.env["bf.shift.assignment"].create(vals)
        return {"type": "ir.actions.act_window_close"}
