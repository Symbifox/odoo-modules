from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

from .common import CHANNELS

#: What a booking is born with; its buttons move them.
BOOKING_SYSTEM_DEFAULTS = {"state": "booked", "late_notice": False, "cancel_late": False, "activity_id": False,
                           "booked_by_id": False, "channel": "office", "cancelled_on": False,
                           "cancelled_by_id": False}
ACTIVITY_SYSTEM_DEFAULTS = {"state": "open"}


class ChildcareBooking(models.Model):
    """An occasional period booked for a child on one date."""

    _name = "bf.school.childcare.booking"
    _inherit = ["bf.school.childcare.guarded", "bf.school.childcare.notify"]
    _description = "Child care booking"
    _order = "date desc, slot_id, id desc"
    _school_system_defaults = BOOKING_SYSTEM_DEFAULTS

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True,
                                 domain=[("is_student", "=", True)])
    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="slot_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="slot_id.company_id", store=True, index=True)
    date = fields.Date(required=True, index=True)
    state = fields.Selection([("booked", "Booked"), ("cancelled", "Cancelled"), ("closed", "Closed day")],
                             default="booked", required=True, readonly=True)
    late_notice = fields.Boolean(
        "Late notice", readonly=True, help="Booked by the office later than the school's notice allows the families.")
    cancel_late = fields.Boolean(
        "Late cancellation", readonly=True,
        help="Cancelled later than the free cancellation notice: the place stays due.")
    activity_id = fields.Many2one("bf.school.childcare.activity", "Activity", readonly=True, ondelete="restrict",
                                  index=True)
    booked_by_id = fields.Many2one("res.partner", "Booked by", readonly=True)
    channel = fields.Selection(CHANNELS, "Received by", default="office", required=True, readonly=True)
    cancelled_on = fields.Datetime(readonly=True)
    cancelled_by_id = fields.Many2one("res.partner", "Cancelled by", readonly=True)

    @api.depends("student_id", "slot_id", "date")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for booking in self:
            booking.display_name = " · ".join(filter(None, [
                booking.student_id.name, format_date(self.env, booking.date) if booking.date else "",
                booking.slot_id.name]))

    def _school_date_label(self):
        self.ensure_one()
        return format_date(self.env, self.date)

    # --- Rules ------------------------------------------------------------------------------

    @api.model
    def _school_check_bookable(self, student, slot, day, channel):
        """Every rule of an occasional booking. Returns the values the system sets on it.

        Refused: a past date, a closed day, a period that does not run that day or is not for
        the child's level, a child who is not a preschool or elementary student of the school
        (s. 62.1), a child already registered or booked for it, a full period, and, for a
        family, less notice than the school asks (the office may still book it: late notice).
        """
        school = slot.school_id
        if day < school._calendar_today():
            raise UserError(_("This date is past."))
        if not school._calendar_childcare_open(day):
            raise UserError(_("Child care is closed on %s.", format_date(self.env, day)))
        if not slot._runs_on(day):
            raise UserError(_("%(slot)s does not run on %(date)s.", slot=slot.display_name,
                              date=format_date(self.env, day)))
        if not student._childcare_levels(school, day):
            raise UserError(_("Child care is for the preschool and elementary students of the school."))
        if not slot._accepts(student, day):
            raise UserError(_("%(slot)s is not for the level of %(student)s.", slot=slot.display_name,
                              student=student.name))
        if student in slot._school_registered(day).registration_id.student_id:
            raise UserError(_("%(student)s is already registered for %(slot)s that day.",
                              student=student.name, slot=slot.display_name))
        activity = slot._school_activity(day)
        late = fields.Datetime.now() + timedelta(hours=school.childcare_booking_notice_hours) > slot._school_start(day)
        if channel == "portal":
            if late:
                raise UserError(_("Book at least %s hours ahead. For a later booking, call the school office.",
                                  school.childcare_booking_notice_hours))
            if activity.deadline and school._calendar_today() > activity.deadline:
                raise UserError(_("Bookings for %s are closed.", activity.name))
        # 🔴 Two families sending the last place at once: the period's row is updated first, so
        # the second one is replayed and sees the first booking (see the slot's _school_lock).
        slot._school_lock()
        if student in slot._school_booked(day).student_id:
            raise UserError(_("%(student)s is already booked for %(slot)s that day.",
                              student=student.name, slot=slot.display_name))
        if not slot._school_has_room(day):
            raise UserError(_("%(slot)s is full on %(date)s.", slot=slot.display_name,
                              date=format_date(self.env, day)))
        return {"late_notice": late, "activity_id": activity.id or False}

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        bookings = self.browse()
        Partner, Slot = self.env["res.partner"].sudo(), self.env["bf.school.childcare.slot"].sudo()
        for vals in vals_list:
            if not self.env.su:
                vals["booked_by_id"] = self.env.user.partner_id.id
            student, slot = Partner.browse(vals.get("student_id")), Slot.browse(vals.get("slot_id"))
            if not (student and slot and vals.get("date")):
                raise ValidationError(_("A booking names a child, a period and a date."))
            vals.update(self._school_check_bookable(student, slot, fields.Date.to_date(vals["date"]),
                                                    vals.get("channel") or "office"))
            # One at a time: the places counted for the next one include this one.
            bookings |= super().create([vals])
        bookings._school_on_booked()
        return bookings

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and {"student_id", "slot_id", "date"} & set(vals):
            raise UserError(_("A booking is not moved: cancel it and book again."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("A booking is cancelled, not deleted: it is the trace of what was asked."))
        return super().unlink()

    # --- Cancelling ---------------------------------------------------------------------------

    def _school_cancel(self, author, channel):
        """Cancelled by the family or the office. Later than the school's free cancellation
        notice, the place stays due ("place reserved, place paid")."""
        now = fields.Datetime.now()
        for booking in self:
            if booking.state != "booked":
                raise UserError(_("This booking is no longer active."))
            start = booking.slot_id._school_start(booking.date)
            if channel == "portal" and now >= start:
                raise UserError(_("This period has started: call the school office."))
            late = now > start - timedelta(hours=booking.school_id.childcare_cancel_notice_hours)
            booking.sudo().write({"state": "cancelled", "cancel_late": late, "cancelled_on": now,
                                  "cancelled_by_id": author.id})
        self._school_on_cancelled()
        return True

    def _school_cancel_free(self, author, reason=None):
        """Cancelled by the school (activity cancelled, covered by a registration): nothing due."""
        self.filtered(lambda b: b.state == "booked").sudo().write({
            "state": "cancelled", "cancel_late": False, "cancelled_on": fields.Datetime.now(),
            "cancelled_by_id": author.id})
        self._school_on_cancelled()

    def _school_close(self):
        """The school closed that day: the booking is closed, nothing due."""
        self.filtered(lambda b: b.state == "booked").sudo().write({"state": "closed", "cancel_late": False})
        self._school_on_cancelled()

    def action_cancel(self):
        """The office cancels a booking."""
        self.check_access("write")
        self.filtered(lambda b: b.state == "booked")._school_cancel(self.env.user.partner_id, "office")
        return True

    def _school_on_booked(self):
        """Extension point: bookings were made (billing modules start here)."""
        return True

    def _school_on_cancelled(self):
        """Extension point: bookings were cancelled or closed (see `cancel_late`)."""
        return True

    # --- The family's side (portal) ---------------------------------------------------------

    @api.model
    def _school_book_by(self, partner, student, slot, day):
        """A family books. Every check lives here and in create; the controller only calls it."""
        if student not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority books child care."))
        # 🔴 The portal catches the error to show it: without a savepoint, a booking refused for
        # a full period would stay written (a caught error does not roll back the request).
        with self.env.cr.savepoint():
            return self.sudo().create({"student_id": student.id, "slot_id": slot.id, "date": day,
                                       "channel": "portal", "booked_by_id": partner.id})

    def _school_cancel_by(self, partner):
        self.ensure_one()
        booking = self.sudo()
        if booking.student_id not in partner._school_authority_links().student_id:
            raise AccessError(_("Only an adult with parental authority cancels child care."))
        return booking._school_cancel(partner, "portal")


class ChildcareActivity(models.Model):
    """What a day without class offers (outing, theme day): its places and its minimum."""

    _name = "bf.school.childcare.activity"
    _inherit = ["bf.school.childcare.guarded"]
    _description = "Child care activity"
    _order = "date desc, id desc"
    _school_system_defaults = ACTIVITY_SYSTEM_DEFAULTS

    name = fields.Char(required=True)
    description = fields.Text(help="Shown to the families on the portal.")
    slot_id = fields.Many2one("bf.school.childcare.slot", "Period", required=True, ondelete="restrict",
                              domain="[('kind', '=', 'full_day')]")
    school_id = fields.Many2one(related="slot_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="slot_id.company_id", store=True, index=True)
    date = fields.Date(required=True, index=True)
    places = fields.Integer(help="0: the places of the period.")
    min_registrations = fields.Integer("Minimum", help="Below this number of children, the activity does "
                                                       "not take place.")
    deadline = fields.Date("Book by", help="Families book on the portal until this date.")
    state = fields.Selection([("open", "Open"), ("confirmed", "Confirmed"), ("cancelled", "Cancelled")],
                             default="open", required=True, readonly=True)
    booking_ids = fields.One2many("bf.school.childcare.booking", "activity_id", "Bookings")
    booked_count = fields.Integer("Booked places", compute="_compute_booked_count")

    @api.depends("name", "date")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for activity in self:
            activity.display_name = " · ".join(filter(None, [
                activity.name, format_date(self.env, activity.date) if activity.date else ""]))

    @api.depends("booking_ids.state")
    def _compute_booked_count(self):
        for activity in self:
            activity.booked_count = len(activity.booking_ids.filtered(lambda b: b.state == "booked"))

    @api.constrains("slot_id", "date", "deadline", "places", "min_registrations", "state")
    def _check_activity(self):
        for activity in self:
            if activity.slot_id.kind != "full_day":
                raise ValidationError(_("An activity takes place on a day without class."))
            if activity.deadline and activity.deadline > activity.date:
                raise ValidationError(_("Bookings close on or before the day of the activity."))
            if activity.places < 0 or activity.min_registrations < 0:
                raise ValidationError(_("The places and the minimum are zero or more."))
            if activity.state != "cancelled" and self.search_count([
                    ("id", "!=", activity.id), ("slot_id", "=", activity.slot_id.id),
                    ("date", "=", activity.date), ("state", "!=", "cancelled")]):
                raise ValidationError(_("This period already has an activity that day."))

    @api.model_create_multi
    def create(self, vals_list):
        self._school_guard_create(vals_list)
        activities = super().create(vals_list)
        # 🔴 The families who booked the day before the activity was planned are part of it: counted,
        # and cancelled at no charge with the others.
        Booking = self.env["bf.school.childcare.booking"].sudo()
        for activity in activities:
            Booking.search([("slot_id", "=", activity.slot_id.id), ("date", "=", activity.date),
                            ("state", "=", "booked"), ("activity_id", "=", False)]).write({"activity_id": activity.id})
        return activities

    def write(self, vals):
        self._school_guard_write(vals)
        if not self.env.su and {"slot_id", "date"} & set(vals) and any(a.booking_ids for a in self):
            raise UserError(_("An activity with bookings is not moved: cancel it and plan another."))
        return super().write(vals)

    def action_confirm(self):
        for activity in self:
            activity.check_access("write")
            if activity.state != "open":
                raise UserError(_("Only an open activity is confirmed."))
            if activity.booked_count < activity.min_registrations:
                raise UserError(_("%(count)s child(ren) booked, %(min)s needed: wait, or cancel the activity.",
                                  count=activity.booked_count, min=activity.min_registrations))
            activity.sudo().write({"state": "confirmed"})
        return True

    def action_cancel(self):
        """The activity does not take place: its bookings are cancelled at no charge and every
        adult with parental authority is told."""
        for activity in self:
            activity.check_access("write")
            if activity.state == "cancelled":
                continue
            bookings = activity.booking_ids.filtered(lambda b: b.state == "booked")
            activity.sudo().write({"state": "cancelled"})
            bookings._school_cancel_free(self.env.user.partner_id)
            bookings._school_notify_authority("bf_school_childcare.mail_template_childcare_activity_cancelled")
        return True
