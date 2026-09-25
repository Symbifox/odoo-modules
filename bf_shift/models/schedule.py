from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..lib import engine
from .tools import internal, notify_each_in_their_language, post, to_local, tz_of


class BfShiftSchedule(models.Model):
    _name = "bf.shift.schedule"
    _description = "Work schedule"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, id desc"

    name = fields.Char(required=True, tracking=True)
    date_from = fields.Date("From", required=True, tracking=True)
    date_to = fields.Date("To", required=True, tracking=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company,
                                 required=True)
    department_id = fields.Many2one("hr.department")
    user_id = fields.Many2one("res.users", string="Responsible",
                              default=lambda self: self.env.user, tracking=True)
    state = fields.Selection([("draft", "Draft"), ("published", "Published"),
                              ("closed", "Closed")],
                             default="draft", required=True, tracking=True, copy=False)
    published_at = fields.Datetime(readonly=True, copy=False)
    assignment_ids = fields.One2many("bf.shift.assignment", "schedule_id", string="Shifts")
    assignment_count = fields.Integer(compute="_compute_counts")
    open_count = fields.Integer("Open shifts", compute="_compute_counts")
    warning_ids = fields.One2many("bf.shift.warning", "schedule_id", string="Warnings")
    warning_count = fields.Integer(compute="_compute_counts")
    checked_at = fields.Datetime(readonly=True, copy=False)
    change_ids = fields.One2many("bf.shift.change", "schedule_id", string="Changes")
    change_count = fields.Integer(compute="_compute_counts")
    note = fields.Html()

    @api.depends("assignment_ids.state", "assignment_ids.employee_id", "warning_ids",
                 "change_ids")
    def _compute_counts(self):
        for rec in self:
            live = rec.assignment_ids.filtered(lambda a: a.state != "cancelled")
            rec.assignment_count = len(live)
            rec.open_count = len(live.filtered("is_open"))
            rec.warning_count = len(rec.warning_ids)
            rec.change_count = len(rec.change_ids)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(_("The schedule ends before it starts."))

    def unlink(self):
        if any(rec.state != "draft" for rec in self):
            raise UserError(_("A published schedule is kept: close it instead. "
                              "Its changes are the evidence in a grievance."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _run_checks(self):
        """Recompute the warnings of each schedule. Returns them."""
        Warning = self.env["bf.shift.warning"].sudo()
        now = fields.Datetime.now()
        for sched in self:
            sched.warning_ids.sudo().unlink()
            vals = []
            live = sched.assignment_ids.filtered(lambda a: a.state != "cancelled")
            for assignment in live.filtered("is_open"):
                vals.append({
                    "schedule_id": sched.id, "assignment_id": assignment.id,
                    "code": "open",
                    "message": _("Open shift: %(when)s has nobody yet.",
                                 when=assignment._when_label()),
                })
            for employee in live.employee_id:
                mine = live.filtered(lambda a: a.employee_id == employee)
                vals += sched._employee_warnings(employee, mine, now)
            Warning.create(vals)
            sched.sudo().checked_at = now
        return self.warning_ids

    def _employee_warnings(self, employee, mine, now):
        self.ensure_one()
        emp = employee.sudo()
        tz = tz_of(emp, self.env)
        agreement = emp.shift_agreement_id
        params = agreement._effective_params()
        # Whole weeks around the schedule, plus a day for the 24-hour window,
        # so the weekly limits see shifts from neighbouring schedules too.
        first = engine.week_start_of(self.date_from, params.week_start) - timedelta(days=1)
        last = engine.week_start_of(self.date_to, params.week_start) + timedelta(days=8)
        others = self.env["bf.shift.assignment"].sudo().search([
            ("employee_id", "=", emp.id),
            ("state", "!=", "cancelled"),
            ("date", ">=", first), ("date", "<=", last),
            ("id", "not in", mine.ids),
            "|", ("schedule_id.state", "in", ("published", "closed")),
            ("schedule_id", "=", self.id),
        ])
        segments = []
        for a in mine:
            informed = a.informed_at or now
            segments.append(a._to_segment(tz, informed_at=to_local(informed, tz)))
        segments += [a._to_segment(tz) for a in others]
        unavailable = self.env["bf.shift.availability"].sudo()._unavailable_intervals(
            emp, first, last)
        warns = engine.check_segments(
            segments, params,
            usual_day_hours=emp._shift_usual_day_hours(),
            flexible=emp.shift_flexible_hours,
            availability_required=emp.shift_availability_required,
            unavailable=unavailable,
            only_keys=set(mine.ids),
        )
        Assignment = self.env["bf.shift.assignment"]
        out = []
        for w in warns:
            assignment = Assignment.browse(w.key)
            out.append({
                "schedule_id": self.id,
                "assignment_id": assignment.id,
                "employee_id": emp.id,
                "code": w.code,
                "message": assignment._warning_message(w),
            })
        return out

    def action_check(self):
        self._run_checks()
        return True

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def action_publish(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only a draft schedule can be published."))
        if not self.assignment_ids.filtered(lambda a: a.state != "cancelled"):
            raise UserError(_("The schedule has no shift."))
        self._run_checks()
        wizard = self.env["bf.shift.publish.wizard"].create({"schedule_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Publish the schedule"),
            "res_model": "bf.shift.publish.wizard",
            "res_id": wizard.id,
            "views": [[False, "form"]],
            "target": "new",
        }

    def _do_publish(self, notify=True):
        """Publish: from now on, every change is logged and the employees
        are told. The warnings do not block: the employee keeps the right to
        refuse, which the change log and the warnings document."""
        now = fields.Datetime.now()
        for sched in self:
            if sched.state != "draft":
                raise UserError(_("Only a draft schedule can be published."))
            live = sched.assignment_ids.filtered(lambda a: a.state != "cancelled")
            internal(live.filtered(lambda a: not a.informed_at and a.employee_id),
                     no_log=True).write({"informed_at": now})
            sched.write({"state": "published", "published_at": now})
            warnings = sched.warning_ids
            body = _("Schedule published with %(count)s shift(s).", count=len(live))
            if warnings:
                body += " " + _("%(count)s warning(s) were reviewed before publishing.",
                                count=len(warnings))
            post(sched, body=body)
            if notify:
                sched._notify_employees(live.employee_id)
        return True

    def _notify_employees(self, employees):
        partners = employees.sudo().user_id.partner_id
        if partners:
            notify_each_in_their_language(self, partners, lambda env: (
                env._("Your schedule %(name)s is published.", name=self.name),
                self.name,
                env._("Schedule"),
            ))

    def action_close(self):
        for sched in self:
            if sched.state != "published":
                raise UserError(_("Only a published schedule can be closed."))
        self.write({"state": "closed"})
        return True

    def action_reopen(self):
        for sched in self:
            if sched.state != "closed":
                raise UserError(_("Only a closed schedule can be reopened."))
        self.write({"state": "published"})
        for sched in self:
            post(sched, body=_("Schedule reopened."))
        return True

    def action_copy_next(self):
        """Copy the schedule to the period that follows, as a draft."""
        self.ensure_one()
        length = (self.date_to - self.date_from).days + 1
        shift = timedelta(days=length)
        new = self.copy({
            "name": _("%(name)s (next)", name=self.name),
            "date_from": self.date_from + shift,
            "date_to": self.date_to + shift,
        })
        for a in self.assignment_ids.filtered(lambda a: a.state != "cancelled"):
            a.copy({
                "schedule_id": new.id,
                "start": a.start + shift,
                "end": a.end + shift,
                "state": "planned",
            })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": new.id,
            "views": [[False, "form"]],
        }

    def action_view_assignments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Shifts"),
            "res_model": "bf.shift.assignment",
            "views": [[False, "calendar"], [False, "list"], [False, "form"]],
            "domain": [("schedule_id", "=", self.id)],
            "context": {"default_schedule_id": self.id},
        }

    def action_view_changes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Changes"),
            "res_model": "bf.shift.change",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("schedule_id", "=", self.id)],
        }

    def action_generate(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add shifts"),
            "res_model": "bf.shift.generate.wizard",
            "views": [[False, "form"]],
            "target": "new",
            "context": {"default_schedule_id": self.id,
                        "default_date_from": self.date_from,
                        "default_date_to": self.date_to},
        }

    def _local_day_bounds(self, tz):
        start = datetime.combine(self.date_from, time(0, 0))
        end = datetime.combine(self.date_to + timedelta(days=1), time(0, 0))
        return start, end
