from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class MeetingSession(models.Model):
    """A parent-teacher meeting evening (or day), for some groups."""

    _name = "bf.school.meeting.session"
    _description = "Parent-teacher meetings"
    _inherit = ["mail.thread"]
    _order = "booking_close desc, id desc"

    name = fields.Char(required=True, tracking=True, help="For example: Meetings of the first report card.")
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict")
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    group_ids = fields.Many2many(
        "bf.school.group", string="Groups", required=True,
        domain="[('school_id', '=', school_id), ('year_id.state', '=', 'current')]")
    slot_minutes = fields.Integer("Slot length (minutes)", default=10, required=True)
    location = fields.Char()
    booking_open = fields.Datetime("Booking opens", required=True)
    booking_close = fields.Datetime("Booking closes", required=True,
                                    help="Families book and cancel until then.")
    state = fields.Selection(
        [("draft", "Draft"), ("open", "Open"), ("closed", "Closed")],
        default="draft", required=True, tracking=True)
    availability_ids = fields.One2many("bf.school.meeting.availability", "session_id", "Teachers' hours")
    slot_ids = fields.One2many("bf.school.meeting.slot", "session_id", "Slots")
    slot_count = fields.Integer(compute="_compute_counts")
    booked_count = fields.Integer("Booked", compute="_compute_counts")

    @api.depends("slot_ids.student_id")
    def _compute_counts(self):
        for session in self:
            session.slot_count = len(session.slot_ids)
            session.booked_count = len(session.slot_ids.filtered("student_id"))

    @api.constrains("slot_minutes", "booking_open", "booking_close")
    def _check_values(self):
        for session in self:
            if session.slot_minutes < 5:
                raise ValidationError(_("A slot lasts at least 5 minutes."))
            if session.booking_close <= session.booking_open:
                raise ValidationError(_("Booking closes after it opens."))

    def _is_booking_open(self):
        self.ensure_one()
        now = fields.Datetime.now()
        return self.state == "open" and self.booking_open <= now <= self.booking_close

    def action_open(self):
        for session in self:
            if not session.slot_ids:
                raise UserError(_("Generate the slots before opening the booking."))
        self.write({"state": "open"})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_generate_slots(self):
        """Cut every teacher's hours into slots. Booked slots are never touched."""
        Slot = self.env["bf.school.meeting.slot"]
        for session in self:
            session.slot_ids.filtered(lambda s: not s.student_id).unlink()
            step = timedelta(minutes=session.slot_minutes)
            for availability in session.availability_ids:
                start = availability.start
                while start + step <= availability.stop:
                    taken = session.slot_ids.filtered(
                        lambda s: s.teacher_id == availability.teacher_id and s.start < start + step
                        and s.stop > start)
                    if not taken:
                        Slot.create({"session_id": session.id, "teacher_id": availability.teacher_id.id,
                                     "start": start, "stop": start + step})
                    start += step
        return True

    # --- What a family sees --------------------------------------------------------------

    @api.model
    def _school_offers_for(self, partner):
        """[(session, student, teacher)] this adult may book: open sessions, their
        children's groups, the teachers of those groups."""
        links = partner._school_portal_links().filtered("receives_notices")
        offers = []
        sessions = self.sudo().search([("state", "=", "open")]).filtered(lambda s: s._is_booking_open())
        for session in sessions:
            for student in links.student_id:
                groups = student.student_enrollment_ids.filtered(
                    lambda e: e.state == "active" and e.year_id.state == "current").group_id & session.group_ids
                for teacher in groups.teacher_ids:
                    offers.append((session, student, teacher))
        return offers


class MeetingAvailability(models.Model):
    """A teacher's hours for a session, cut into slots."""

    _name = "bf.school.meeting.availability"
    _description = "Teacher's hours for parent-teacher meetings"
    _order = "session_id, teacher_id, start"

    session_id = fields.Many2one("bf.school.meeting.session", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="session_id.company_id", store=True)
    teacher_id = fields.Many2one("res.users", "Teacher", required=True, domain=[("share", "=", False)])
    start = fields.Datetime(required=True)
    stop = fields.Datetime(required=True)

    @api.constrains("start", "stop")
    def _check_dates(self):
        for availability in self:
            if availability.stop <= availability.start:
                raise ValidationError(_("The hours end after they start."))


class MeetingSlot(models.Model):
    """One meeting slot: a teacher, a time, and the student it is booked for."""

    _name = "bf.school.meeting.slot"
    _description = "Parent-teacher meeting slot"
    _order = "start, teacher_id"
    _rec_name = "start"

    session_id = fields.Many2one("bf.school.meeting.session", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="session_id.company_id", store=True)
    teacher_id = fields.Many2one("res.users", "Teacher", required=True, index=True)
    start = fields.Datetime(required=True)
    stop = fields.Datetime(required=True)
    student_id = fields.Many2one("res.partner", "Student", ondelete="set null", index=True)
    partner_id = fields.Many2one("res.partner", "Booked by", ondelete="set null")
    booked_on = fields.Datetime(readonly=True)

    _sql_constraints = [
        ("one_per_student_teacher", "UNIQUE(session_id, teacher_id, student_id)",
         "A student meets each teacher once per session."),
    ]

    def _school_book(self, student, partner):
        """Book this slot for this student, by this adult. Every check lives here."""
        self.ensure_one()
        session = self.session_id
        if not session._is_booking_open():
            raise UserError(_("Booking is closed."))
        if self.student_id:
            raise UserError(_("This slot was just taken. Choose another one."))
        offers = session._school_offers_for(partner)
        if (session, student, self.teacher_id) not in offers:
            raise UserError(_("This teacher does not teach this child in these meetings."))
        if self.sudo().search_count([("session_id", "=", session.id), ("teacher_id", "=", self.teacher_id.id),
                                     ("student_id", "=", student.id)]):
            raise UserError(_("A meeting with this teacher is already booked for this child."))
        # 🔴 One adult cannot be in two rooms at once: no overlap with their own bookings.
        if self.sudo().search_count([("session_id", "=", session.id), ("partner_id", "=", partner.id),
                                     ("start", "<", self.stop), ("stop", ">", self.start)]):
            raise UserError(_("You already have a meeting at that time."))
        # 🔴 Two parents clicking the same slot at once both passed the "free" check above;
        # the last write silently took the slot from the first. The database decides:
        # the update only lands on a slot that is still free.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_school_meeting_slot SET student_id = %s, partner_id = %s, booked_on = %s "
            "WHERE id = %s AND student_id IS NULL",
            [student.id, partner.id, fields.Datetime.now(), self.id])
        if not self.env.cr.rowcount:
            raise UserError(_("This slot was just taken. Choose another one."))
        self.invalidate_recordset(["student_id", "partner_id", "booked_on"])
        self._school_notify(partner)
        return True

    def _school_cancel(self, partner):
        self.ensure_one()
        if not self.session_id._is_booking_open():
            raise UserError(_("Booking is closed: contact the school office to cancel."))
        if self.partner_id != partner:
            raise UserError(_("Only the adult who booked cancels."))
        self.sudo().write({"student_id": False, "partner_id": False, "booked_on": False})
        return True

    def _school_notify(self, partner):
        template = self.env.ref("bf_school_meeting.mail_template_meeting_booked", raise_if_not_found=False)
        if not template or not partner.email:
            return
        lang = partner.lang or self.company_id.partner_id.lang or "fr_CA"
        template.sudo().with_context(lang=lang, school_lang=lang).send_mail(
            self.id, force_send=False,
            email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
            email_values={"recipient_ids": [(6, 0, partner.ids)], "email_to": False})
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
