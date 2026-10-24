from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

from .common import CHANNELS, DAY_FIELDS

#: What a registration is born with; the buttons move them.
REGISTRATION_SYSTEM_DEFAULTS = {"state": "draft", "requested_by_id": False, "channel": "office",
                                "confirmed_on": False, "confirmed_by_id": False}
#: What the family and the school agreed on: frozen once the registration leaves the draft.
REGISTRATION_TERMS = {"student_id", "school_id", "year_id", "date_start", "date_end", "line_ids"}


class ChildcareRegistration(models.Model):
    """A child's regular place at child care: some periods, some weekdays, between two dates."""

    _name = "bf.school.childcare.registration"
    _inherit = ["mail.thread", "mail.activity.mixin", "bf.school.childcare.guarded",
                "bf.school.childcare.notify"]
    _description = "Child care registration"
    _order = "date_start desc, id desc"
    _school_system_defaults = REGISTRATION_SYSTEM_DEFAULTS

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True,
                                 domain=[("is_student", "=", True)], tracking=True)
    school_id = fields.Many2one("bf.school", "School", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True, index=True)
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict",
                              domain="[('school_id', '=', school_id)]")
    date_start = fields.Date("From", required=True, tracking=True)
    date_end = fields.Date("Until", required=True, tracking=True)
    state = fields.Selection([("draft", "Requested"), ("active", "In force"), ("ended", "Ended"),
                              ("cancelled", "Cancelled")], default="draft", required=True, readonly=True,
                             tracking=True, copy=False)
    line_ids = fields.One2many("bf.school.childcare.registration.line", "registration_id", "Periods", copy=True)
    periods_per_week = fields.Integer(
        "Periods per week", compute="_compute_periods_per_week", store=True,
        help="Each period ticked on each weekday counts once: after school four days a week is 4.")
    requested_by_id = fields.Many2one("res.partner", "Requested by", readonly=True, copy=False)
    channel = fields.Selection(CHANNELS, "Received by", default="office", required=True, readonly=True)
    confirmed_on = fields.Datetime(readonly=True, copy=False)
    confirmed_by_id = fields.Many2one("res.users", "Confirmed by", readonly=True, copy=False)
    note = fields.Text(help="Internal. Never shown to the family.")

    @api.depends("student_id", "year_id")
    def _compute_display_name(self):
        for registration in self:
            registration.display_name = " · ".join(filter(None, [
                registration.student_id.name, registration.year_id.name]))

    @api.depends("line_ids.mon", "line_ids.tue", "line_ids.wed", "line_ids.thu", "line_ids.fri",
                 "line_ids.slot_id.mon", "line_ids.slot_id.tue", "line_ids.slot_id.wed", "line_ids.slot_id.thu",
                 "line_ids.slot_id.fri")
    def _compute_periods_per_week(self):
        for registration in self:
            registration.periods_per_week = sum(line._school_day_count() for line in registration.line_ids)

    @api.onchange("year_id")
    def _onchange_year_id(self):
        if self.year_id:
            today = self.year_id.school_id._calendar_today()
            self.date_start = max(self.year_id.date_start, today)
            self.date_end = self.year_id.date_end

    # --- Rules ------------------------------------------------------------------------------

    @api.constrains("school_id", "year_id", "date_start", "date_end")
    def _check_dates(self):
        for registration in self:
            if registration.year_id.school_id != registration.school_id:
                raise ValidationError(_("The school year belongs to another school."))
            if registration.date_end < registration.date_start:
                raise ValidationError(_("A registration ends after it starts."))
            if registration.date_start < registration.year_id.date_start \
                    or registration.date_end > registration.year_id.date_end:
                raise ValidationError(_("A registration stays within its school year."))

    @api.constrains("student_id", "school_id", "date_start", "date_end", "state")
    def _check_one_in_force(self):
        for registration in self.filtered(lambda r: r.state in ("draft", "active")):
            twin = self.sudo().search_count([
                ("id", "!=", registration.id), ("student_id", "=", registration.student_id.id),
                ("state", "in", ("draft", "active")),
                ("date_start", "<=", registration.date_end), ("date_end", ">=", registration.date_start)])
            if twin:
                raise ValidationError(_(
                    "%s already has a child care registration or a request over these dates: end it first.",
                    registration.student_id.name))

    @api.constrains("student_id", "school_id", "date_start", "line_ids")
    def _check_eligible(self):
        for registration in self.filtered(lambda r: r.state in ("draft", "active")):
            registration._school_check_eligible()

    def _school_check_eligible(self):
        """S. 62.1: a preschool or elementary student of the school, at the levels of its periods."""
        self.ensure_one()
        student, day = self.student_id, self.date_start
        if not student._childcare_levels(self.school_id, day):
            raise ValidationError(_(
                "Child care is for the preschool and elementary students of the school: %s has no such "
                "enrolment on %s.", student.name, format_date(self.env, day)))
        for line in self.line_ids:
            if line.slot_id.school_id != self.school_id:
                raise ValidationError(_("%s is a period of another school.", line.slot_id.display_name))
            if not line.slot_id._accepts(student, day):
                raise ValidationError(_("%(slot)s is not for the level of %(student)s.",
                                        slot=line.slot_id.display_name, student=student.name))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and REGISTRATION_TERMS & set(vals) and any(r.state != "draft" for r in self):
            raise UserError(_("A registration in force is not edited: end it and register again."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(r.state != "draft" for r in self):
            raise UserError(_("Only a request is deleted; a registration in force is ended."))
        return super().unlink()

    # --- Buttons ----------------------------------------------------------------------------

    def action_activate(self):
        """The manager accepts the request: places checked on every date, then the family is told."""
        for registration in self:
            registration.check_access("write")
            if registration.state != "draft":
                raise UserError(_("Only a request is put in force."))
            if not registration.line_ids:
                raise UserError(_("Choose at least one period."))
            registration._school_check_eligible()
            registration.line_ids.slot_id._school_lock()
            registration._school_absorb_bookings()
            registration._school_check_places()
            registration.sudo().write({"state": "active", "confirmed_on": fields.Datetime.now(),
                                       "confirmed_by_id": self.env.uid})
            registration.activity_ids.sudo().action_feedback(feedback=_("Registration put in force."))
            registration._school_notify_authority(
                "bf_school_childcare.mail_template_childcare_registration_confirmed")
            registration._school_on_activated()
        return True

    def _school_dates(self, line, start=None):
        """The dates of this registration when `line`'s period runs and its weekday is ticked."""
        self.ensure_one()
        day = max(start or self.date_start, self.date_start)
        while day <= self.date_end:
            if line._weekday_ticked(day) and line.slot_id._runs_on(day):
                yield day
            day += timedelta(days=1)

    def _school_absorb_bookings(self):
        """Occasional bookings the registration now covers are cancelled, at no charge: the child
        would otherwise hold two places."""
        self.ensure_one()
        Booking = self.env["bf.school.childcare.booking"].sudo()
        # 🔴 From today: a past booking was used (the child came), it is not cancelled for free.
        start = max(self.date_start, self.school_id._calendar_today())
        for line in self.line_ids:
            bookings = Booking.search([("student_id", "=", self.student_id.id), ("slot_id", "=", line.slot_id.id),
                                       ("state", "=", "booked"), ("date", ">=", start),
                                       ("date", "<=", self.date_end)])
            bookings.filtered(lambda b: line._weekday_ticked(b.date))._school_cancel_free(
                self.env.user.partner_id, _("Covered by the registration."))

    def _school_check_places(self):
        """🔴 Every date from today: a regular place taken on a full period refused a family who
        had booked it first."""
        self.ensure_one()
        today = self.school_id._calendar_today()
        for line in self.line_ids:
            for day in self._school_dates(line, start=today):
                if not line.slot_id._school_has_room(day):
                    raise UserError(_("%(slot)s is full on %(date)s (%(places)s places).",
                                      slot=line.slot_id.display_name, date=format_date(self.env, day),
                                      places=line.slot_id._school_capacity(day)))

    def action_end(self):
        """The registration stops after today (school's clock); one that has not started is cancelled."""
        for registration in self:
            registration.check_access("write")
            if registration.state != "active":
                raise UserError(_("Only a registration in force is ended."))
            today = registration.school_id._calendar_today()
            if today < registration.date_start:
                registration.sudo().write({"state": "cancelled"})
            else:
                registration.sudo().write({"state": "ended", "date_end": min(today, registration.date_end)})
        return True

    def action_cancel(self):
        for registration in self:
            registration.check_access("write")
            if registration.state != "draft":
                raise UserError(_("Only a request is cancelled; a registration in force is ended."))
            registration.sudo().write({"state": "cancelled"})
            registration.activity_ids.sudo().unlink()
        return True

    def _school_on_activated(self):
        """Extension point: a registration was put in force (billing modules start here)."""
        return True

    # --- Read by the family and the emails --------------------------------------------------

    def _school_schedule_lines(self):
        """["After school (15:30-18:00): Monday, Tuesday"], in the reader's language."""
        self.ensure_one()
        return [_("%(period)s: %(days)s", period=line.slot_id.display_name, days=line._weekday_label())
                for line in self.line_ids]

    def _school_period_label(self):
        self.ensure_one()
        return _("from %(start)s to %(end)s", start=format_date(self.env, self.date_start),
                 end=format_date(self.env, self.date_end))

    # --- The family's request (portal) ------------------------------------------------------

    @api.model
    def _school_request_by(self, partner, student, date_start, choices):
        """An adult with parental authority asks for a registration. `choices`: {slot: [day fields]}.

        The request waits for the manager, who puts it in force; nothing is reserved before.
        """
        if student not in partner._school_authority_links().student_id:
            raise UserError(_("Only an adult with parental authority asks for a registration."))
        school = student._childcare_school(date_start)
        if not school:
            raise UserError(_("Child care is for the preschool and elementary students of the school."))
        if date_start < school._calendar_today():
            raise UserError(_("A registration starts today or later."))
        year = self.env["bf.school.year"].sudo().search([
            ("school_id", "=", school.id), ("date_start", "<=", date_start), ("date_end", ">=", date_start)],
            limit=1)
        lines = []
        for slot, days in choices.items():
            if slot.school_id != school or not slot.active:
                raise UserError(_("This period is not offered at the school."))
            days = [d for d in days if d in DAY_FIELDS and slot[d]]
            if days:
                lines.append((0, 0, {"slot_id": slot.id, **{d: True for d in days}}))
        if not lines:
            raise UserError(_("Choose at least one period and one day."))
        with self.env.cr.savepoint():
            registration = self.sudo().create({
                "student_id": student.id, "school_id": school.id, "year_id": year.id,
                "date_start": date_start, "date_end": year.date_end, "line_ids": lines,
                "channel": "portal", "requested_by_id": partner.id})
        responsible = school._childcare_responsible()
        if responsible:
            registration.activity_schedule(
                "mail.mail_activity_data_todo", user_id=responsible.id,
                summary=_("Child care registration requested"),
                note=_("Requested on the family portal by %s.", partner.name))
        registration.message_post(body=_("Requested on the family portal by %s.", partner.name))
        return registration


class ChildcareRegistrationLine(models.Model):
    """One period of a registration and the weekdays the child attends it."""

    _name = "bf.school.childcare.registration.line"
    _inherit = ["bf.school.childcare.weekdays"]
    _description = "Period of a child care registration"
    _order = "registration_id, slot_id"

    registration_id = fields.Many2one("bf.school.childcare.registration", required=True, ondelete="cascade",
                                      index=True)
    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", required=True, ondelete="restrict",
                              index=True)
    company_id = fields.Many2one(related="registration_id.company_id", store=True)

    _sql_constraints = [("one_line_per_slot", "UNIQUE(registration_id, slot_id)",
                         "A period appears once in a registration.")]

    @api.constrains("slot_id", "mon", "tue", "wed", "thu", "fri")
    def _check_days(self):
        for line in self:
            if not line._weekday_count():
                raise ValidationError(_("Tick at least one day for %s.", line.slot_id.display_name))
            if any(line[day] and not line.slot_id[day] for day in DAY_FIELDS):
                raise ValidationError(_("%s does not run on one of the days ticked.", line.slot_id.display_name))

    def _school_day_count(self):
        """The days ticked on which the period runs (a day unticked on the period later stops counting)."""
        self.ensure_one()
        return sum(1 for day in DAY_FIELDS if self[day] and self.slot_id[day])

    def _school_check_editable(self, registrations):
        # 🔴 A line added by RPC to a registration in force took a place no one had checked.
        if not self.env.su and any(r.state != "draft" for r in registrations):
            raise UserError(_("A registration in force is not edited: end it and register again."))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_check_editable(self.env["bf.school.childcare.registration"].browse(
            [vals.get("registration_id") for vals in vals_list if vals.get("registration_id")]))
        return super().create(vals_list)

    def write(self, vals):
        registrations = self.registration_id
        if "registration_id" in vals:
            registrations |= registrations.browse(vals["registration_id"])
        self._school_check_editable(registrations)
        return super().write(vals)

    def unlink(self):
        self._school_check_editable(self.registration_id)
        return super().unlink()
