from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..lib import engine
from .tools import (flag, fmt_num, internal, lang_of, local_bounds, notify_each_in_their_language,
                    to_local, to_utc, tz_of)

KINDS = [
    ("work", "Shift"),
    ("callback", "Call-back"),
    ("on_call", "On call at home"),
    ("leave", "Paid leave or holiday"),
    ("bank_leave", "Time taken from the bank"),
]

# Fields whose change, once the schedule is published, is logged and told.
LOGGED = ("employee_id", "start", "end", "kind", "state", "break_minutes")
# Fields that can still be written on a closed schedule (actual hours).
AFTER_CLOSE = {"actual_start", "actual_end", "actual_break_minutes", "state", "note"}


class BfShiftAssignment(models.Model):
    _name = "bf.shift.assignment"
    _description = "Shift"
    _inherit = ["mail.thread"]
    _order = "start, id"

    name = fields.Char(compute="_compute_name", store=True)
    schedule_id = fields.Many2one("bf.shift.schedule", required=True, ondelete="cascade",
                                  index=True)
    schedule_state = fields.Selection(related="schedule_id.state", store=True,
                                      string="Schedule status")
    company_id = fields.Many2one(related="schedule_id.company_id", store=True)
    employee_id = fields.Many2one("hr.employee", index=True, tracking=True,
                                  help="Empty: an open shift, to be offered.")
    employee_user_id = fields.Many2one("res.users", compute="_compute_employee_user",
                                       store=True, compute_sudo=True)
    template_id = fields.Many2one("bf.shift.template", string="Template")
    job_id = fields.Many2one("hr.job", string="Position")
    kind = fields.Selection(KINDS, default="work", required=True, tracking=True)
    state = fields.Selection([("planned", "Planned"), ("done", "Done"),
                              ("cancelled", "Cancelled")],
                             default="planned", required=True, tracking=True)
    start = fields.Datetime(required=True, tracking=True)
    end = fields.Datetime(required=True, tracking=True)
    date = fields.Date(compute="_compute_date", store=True, index=True,
                       help="Local date the shift starts on.")
    break_minutes = fields.Integer("Break (min)", default=0)
    break_paid = fields.Boolean("Paid break")
    actual_start = fields.Datetime("Actual start")
    actual_end = fields.Datetime("Actual end")
    actual_break_minutes = fields.Integer("Actual break (min)", compute="_compute_actual_break",
                                          store=True, readonly=False)
    planned_hours = fields.Float(compute="_compute_hours", store=True)
    worked_hours = fields.Float(compute="_compute_hours", store=True,
                                help="Actual hours when entered, planned hours otherwise.")
    to_bank = fields.Boolean("Overtime to the bank")
    bank_requested_by_employee = fields.Boolean(
        "Requested by the employee",
        help="The labour standards let the employee choose the bank; the employer cannot impose it.")
    informed_at = fields.Datetime(
        "Employee informed on", copy=False,
        help="When the employee was told of this shift as it stands: at publication, "
        "then at each change.")
    late_notice = fields.Boolean(
        "Short notice", compute="_compute_late_notice", store=True,
        help="Told fewer days ahead than the notice rule: the employee may refuse it.")
    is_open = fields.Boolean("Open shift", compute="_compute_is_open", store=True)
    offer_ids = fields.One2many("bf.shift.offer", "assignment_id", string="Offers")
    change_ids = fields.One2many("bf.shift.change", "assignment_id", string="Changes")
    note = fields.Text()
    color = fields.Integer(related="template_id.color")

    @api.depends("employee_id", "start", "template_id", "kind")
    def _compute_name(self):
        for rec in self:
            who = rec.employee_id.name or _("Open shift")
            kinds = dict(rec._fields["kind"]._description_selection(rec.env))
            what = rec.template_id.name or kinds.get(rec.kind)
            rec.name = "%s · %s" % (who, what)

    @api.depends("employee_id.user_id")
    def _compute_employee_user(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id

    @api.depends("start", "employee_id")
    def _compute_date(self):
        for rec in self:
            rec.date = to_local(rec.start, tz_of(rec.employee_id, self.env)).date() \
                if rec.start else False

    @api.depends("break_minutes", "actual_start")
    def _compute_actual_break(self):
        for rec in self:
            if not rec.actual_start:
                rec.actual_break_minutes = rec.break_minutes

    @api.depends("start", "end", "break_minutes", "break_paid", "actual_start", "actual_end",
                 "actual_break_minutes")
    def _compute_hours(self):
        for rec in self:
            rec.planned_hours = rec._paid(rec.start, rec.end, rec.break_minutes)
            if rec.actual_start and rec.actual_end:
                rec.worked_hours = rec._paid(rec.actual_start, rec.actual_end,
                                             rec.actual_break_minutes)
            else:
                rec.worked_hours = rec.planned_hours

    def _paid(self, start, end, break_minutes):
        if not start or not end:
            return 0.0
        span = (end - start).total_seconds() / 3600.0
        unpaid = 0.0 if self.break_paid else (break_minutes or 0) / 60.0
        return max(0.0, span - unpaid)

    @api.depends("informed_at", "start", "employee_id")
    def _compute_late_notice(self):
        for rec in self:
            if not (rec.informed_at and rec.start and rec.employee_id):
                rec.late_notice = False
                continue
            emp = rec.employee_id.sudo()
            if emp.shift_availability_required:
                rec.late_notice = False
                continue
            days = emp.shift_agreement_id._effective_params().notice_days
            rec.late_notice = (rec.start - rec.informed_at) < timedelta(days=days)

    @api.depends("employee_id", "kind", "state")
    def _compute_is_open(self):
        for rec in self:
            rec.is_open = not rec.employee_id and rec.kind in ("work", "callback") \
                and rec.state != "cancelled"

    @api.constrains("start", "end", "actual_start", "actual_end")
    def _check_times(self):
        for rec in self:
            if rec.end <= rec.start:
                raise ValidationError(_("A shift ends after it starts."))
            if rec.actual_start and rec.actual_end and rec.actual_end <= rec.actual_start:
                raise ValidationError(_("The actual end comes after the actual start."))

    @api.constrains("to_bank", "bank_requested_by_employee", "employee_id")
    def _check_bank(self):
        for rec in self:
            if not rec.to_bank:
                continue
            if not rec.bank_requested_by_employee:
                raise ValidationError(_(
                    "Overtime goes to the bank only at the employee's request: "
                    "the employer cannot impose it."))
            if not rec.employee_id.sudo().shift_agreement_id.bank_allowed:
                raise ValidationError(_("The working conditions of %(name)s have no time bank.",
                                        name=rec.employee_id.name))

    @api.onchange("template_id", "date")
    def _onchange_template(self):
        if not self.template_id:
            return
        day = self.date or (self.schedule_id.date_from if self.schedule_id else None)
        if not day:
            return
        tpl = self.template_id
        tz = tz_of(self.employee_id, self.env)
        start, end = local_bounds(day, tpl.hour_from, tpl.hour_to)
        self.start = to_utc(start, tz)
        self.end = to_utc(end, tz)
        self.break_minutes = tpl.break_minutes
        self.break_paid = tpl.break_paid
        self.job_id = tpl.job_id or self.job_id
        if tpl.kind == "on_call":
            self.kind = "on_call"

    # ------------------------------------------------------------------
    # Engine bridge
    # ------------------------------------------------------------------

    def _to_segment(self, tz, actual=False, informed_at=None):
        self.ensure_one()
        use_actual = actual and self.actual_start and self.actual_end
        start = self.actual_start if use_actual else self.start
        end = self.actual_end if use_actual else self.end
        brk = self.actual_break_minutes if use_actual else self.break_minutes
        return engine.Segment(
            key=self.id,
            start=to_local(start, tz),
            end=to_local(end, tz),
            break_hours=(brk or 0) / 60.0,
            break_paid=self.break_paid,
            kind=self.kind,
            to_bank=self.to_bank,
            informed_at=informed_at if informed_at is not None else (
                to_local(self.informed_at, tz) if self.informed_at else None),
            tz=tz,
        )

    def _when_label(self):
        self.ensure_one()
        tz = tz_of(self.employee_id, self.env)
        start = to_local(self.start, tz)
        end = to_local(self.end, tz)
        return "%s %s–%s" % (start.strftime("%Y-%m-%d"), start.strftime("%H:%M"),
                             end.strftime("%H:%M"))

    def _warning_message(self, w):
        self.ensure_one()
        v = {k: fmt_num(self.env, val) if isinstance(val, (int, float)) else val
             for k, val in w.values.items()}
        when = self._when_label()
        if w.code == "overlap":
            return _("%(when)s overlaps another shift of the same person.", when=when)
        if w.code == "rest_between":
            return _("%(when)s: %(hours)s h of rest since the previous shift, "
                     "the agreement asks for %(limit)s h.", when=when, **v)
        if w.code == "meal":
            return _("%(when)s: no %(minutes)s-minute meal break after %(after)s hours "
                     "(LNT art. 79).", when=when, **v)
        if w.code == "notice":
            return _("%(when)s: told %(days)s day(s) ahead, fewer than %(limit)s. "
                     "The employee may refuse it (LNT art. 59.0.1).", when=when, **v)
        if w.code == "max_24h":
            return _("%(when)s: %(hours)s h within 24 hours, more than %(limit)s. "
                     "The employee may refuse (LNT art. 59.0.1).", when=when, **v)
        if w.code == "daily_extra":
            return _("%(when)s: %(hours)s h that day, beyond the usual day plus 2 hours "
                     "(%(limit)s h). The employee may refuse (LNT art. 59.0.1).", when=when, **v)
        if w.code == "weekly_max":
            return _("Week of %(when)s: %(hours)s h, more than %(limit)s. "
                     "The employee may refuse (LNT art. 59.0.1).", when=when, **v)
        if w.code == "weekly_rest":
            return _("Week of %(when)s: the longest rest is %(hours)s h, fewer than "
                     "%(limit)s consecutive hours (LNT art. 78).", when=when, **v)
        if w.code == "unavailable":
            return _("%(when)s falls in a declared unavailability.", when=when)
        return w.code

    # ------------------------------------------------------------------
    # Logged writes
    # ------------------------------------------------------------------

    def _snapshot(self):
        self.ensure_one()
        return {
            "employee_id": self.employee_id,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "state": self.state,
            "break_minutes": self.break_minutes,
        }

    def _describe(self, snap, env=None):
        """One line for the change log and the notices, in ``env``'s language."""
        env = env or self.env
        tz = tz_of(snap["employee_id"], env)
        start = to_local(snap["start"], tz)
        end = to_local(snap["end"], tz)
        kinds = dict(self._fields["kind"]._description_selection(env))
        return "%s · %s %s–%s · %s%s" % (
            snap["employee_id"].name or env._("Open shift"),
            start.strftime("%Y-%m-%d"), start.strftime("%H:%M"), end.strftime("%H:%M"),
            kinds.get(snap["kind"]),
            " · " + env._("cancelled") if snap["state"] == "cancelled" else "",
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if flag(self.env, "no_log"):
            return records
        now = fields.Datetime.now()
        for rec in records:
            if rec.schedule_id.state == "closed":
                raise UserError(_("The schedule %(name)s is closed.", name=rec.schedule_id.name))
            if rec.schedule_id.state == "published":
                if rec.employee_id:
                    internal(rec, no_log=True).informed_at = now
                rec._log_change("create", None, rec._snapshot())
        return records

    def write(self, vals):
        if flag(self.env, "no_log"):
            return super().write(vals)
        closed = self.filtered(lambda r: r.schedule_id.state == "closed")
        if closed and set(vals) - AFTER_CLOSE:
            raise UserError(_("The schedule is closed: only the actual hours can still change. "
                              "Reopen it to change a shift."))
        logged = set(vals) & set(LOGGED)
        before = {}
        if logged:
            before = {rec.id: rec._snapshot() for rec in self
                      if rec.schedule_id.state in ("published", "closed")}
        res = super().write(vals)
        if before:
            now = fields.Datetime.now()
            for rec in self.filtered(lambda r: r.id in before):
                old = before[rec.id]
                new = rec._snapshot()
                # Marking a shift done is bookkeeping, not a change of schedule.
                if {k: v for k, v in old.items() if k != "state"} == \
                        {k: v for k, v in new.items() if k != "state"} and \
                        "cancelled" not in (old["state"], new["state"]):
                    continue
                if vals.get("state") == "cancelled":
                    kind = "cancel"
                elif old["employee_id"] != new["employee_id"]:
                    kind = "reassign"
                else:
                    kind = "modify"
                if new["state"] != "cancelled" and new["employee_id"] and any(
                        old[k] != new[k] for k in ("employee_id", "start", "end", "kind")):
                    super(BfShiftAssignment, rec).write({"informed_at": now})
                rec._log_change(kind, old, new)
        return res

    def unlink(self):
        if any(rec.schedule_id.state != "draft" for rec in self):
            raise UserError(_("A published shift is not deleted: cancel it, so the change is kept."))
        return super().unlink()

    def _log_change(self, kind, old, new):
        self.ensure_one()
        ctx = self.env.context
        consent = flag(self.env, "consent")
        params = (new["employee_id"] or (old or {}).get("employee_id") or
                  self.env["hr.employee"]).sudo().shift_agreement_id._effective_params()
        start = new["start"]
        late = (start - fields.Datetime.now()) < timedelta(days=params.notice_days)
        affected = new["employee_id"] or (old and old["employee_id"])
        if affected and affected.sudo().shift_availability_required:
            late = False
        if not consent:
            consent = "pending" if (late and kind != "cancel" and new["employee_id"]) else "na"
        # The log is written once, in the language of the person it concerns.
        log_env = self.with_context(lang=lang_of(
            self.env, user=affected.sudo().user_id if affected else None)).env
        change = self.env["bf.shift.change"].sudo().create({
            "assignment_id": self.id,
            "schedule_id": self.schedule_id.id,
            "employee_id": affected.id if affected else False,
            "previous_employee_id": old["employee_id"].id if old and old["employee_id"] else False,
            "change_type": kind,
            "before": self._describe(old, log_env) if old else False,
            "after": self._describe(new, log_env),
            "reason": flag(self.env, "reason") or False,
            "late": late,
            "consent": consent,
            "consent_at": fields.Datetime.now() if consent == "given" else False,
        })
        partners = self.env["res.partner"]
        for emp in (new["employee_id"], old and old["employee_id"]):
            if emp and emp.sudo().user_id:
                partners |= emp.sudo().user_id.partner_id
        if partners and not flag(self.env, "silent"):
            notify_each_in_their_language(self, partners, lambda env: (
                env._("Shift changed: %(after)s", after=self._describe(new, env)),
                self.schedule_id.name,
                env._("Shift"),
            ))
        return change

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_done(self):
        self.write({"state": "done"})
        return True

    def action_offer(self):
        self.ensure_one()
        if not self.is_open:
            raise UserError(_("Only an open shift is offered."))
        if self.schedule_id.state != "published":
            raise UserError(_("Publish the schedule before offering its open shifts."))
        offer = self.env["bf.shift.offer"].create({"assignment_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.shift.offer",
            "res_id": offer.id,
            "views": [[False, "form"]],
        }

    def _check_is_employee_or_manager(self, employee):
        if self.env.user.has_group("bf_shift.group_shift_manager"):
            return "manager"
        if employee and employee.sudo().user_id == self.env.user:
            return "self"
        raise AccessError(_("Only the employee concerned or a shift manager can do this."))
