from datetime import datetime

import pytz

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_school_calendar.models.calendar_day import DEFAULT_TZ

#: Child care is for preschool and elementary students (Act respecting private education,
#: s. 62.1). Secondary students do not attend it.
CHILDCARE_STAGES = ("preschool", "elementary")


class School(models.Model):
    _inherit = "bf.school"

    childcare_booking_notice_hours = fields.Integer(
        "Booking notice (hours)", default=24,
        help="A family books an occasional period on the portal at least this many hours before it "
             "starts. Later, only the office books it, marked as late notice.")
    childcare_cancel_notice_hours = fields.Integer(
        "Free cancellation (hours before)", default=24,
        help="A booking cancelled later than this many hours before the period stays due.")
    childcare_walk_alone_min_age = fields.Integer(
        "Minimum age to leave alone", default=0,
        help="A child younger than this cannot be allowed to leave child care alone. 0: no minimum.")
    childcare_manager_id = fields.Many2one(
        "res.users", "Child care manager", domain=[("share", "=", False)],
        help="Receives the registration requests and the departures to review. Empty: the first "
             "person of the child care manager role.")
    # Only the child care roles read the periods: a teacher opening the school reads nothing of them.
    childcare_slot_ids = fields.One2many(
        "bf.school.childcare.slot", "school_id", "Child care periods",
        groups="bf_school_childcare.group_school_childcare")

    @api.constrains("childcare_booking_notice_hours", "childcare_cancel_notice_hours",
                    "childcare_walk_alone_min_age")
    def _check_childcare_settings(self):
        for school in self:
            if min(school.childcare_booking_notice_hours, school.childcare_cancel_notice_hours,
                   school.childcare_walk_alone_min_age) < 0:
                raise ValidationError(_("The child care notices and the minimum age are zero or more."))

    def _childcare_responsible(self):
        """Who receives the requests and the departures to review."""
        self.ensure_one()
        if self.childcare_manager_id:
            return self.childcare_manager_id
        group = self.env.ref("bf_school_childcare.group_school_childcare_manager")
        users = group.sudo().users.filtered(
            lambda u: u.active and not u.share and u.id != SUPERUSER_ID and self.company_id in u.company_ids)
        return users.sorted("id")[:1]


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _childcare_levels(self, school, day):
        """The preschool and elementary levels of this student at `school` on `day`, read in sudo.

        Empty: the student may not attend child care (s. 62.1: preschool and elementary
        students of the school, enrolled in a group of the year that covers the day). The
        enrolment's own level wins when the enrolment carries one; otherwise the group's levels.

        🔴 A group that mixes stages (Grade 6 and Secondary 1) says nothing of this student: without
        a level of its own, such an enrolment admits no one.
        """
        self.ensure_one()
        enrollments = self.env["bf.school.enrollment"].sudo().search([
            ("student_id", "=", self.id), ("school_id", "=", school.id), ("state", "=", "active"),
            ("year_id.date_start", "<=", day), ("year_id.date_end", ">=", day)])
        levels = self.env["bf.school.level"].sudo()
        for enrollment in enrollments:
            own = "level_id" in enrollment._fields and enrollment.level_id
            if own:
                levels |= own
                continue
            group_levels = enrollment.group_id.level_ids
            if all(level.stage in CHILDCARE_STAGES for level in group_levels):
                levels |= group_levels
        return levels.filtered(lambda level: level.stage in CHILDCARE_STAGES)

    def _childcare_school(self, day):
        """The school whose child care this student may attend on `day` (the first one)."""
        self.ensure_one()
        enrollments = self.env["bf.school.enrollment"].sudo().search([
            ("student_id", "=", self.id), ("state", "=", "active"),
            ("year_id.date_start", "<=", day), ("year_id.date_end", ">=", day)])
        for school in enrollments.school_id:
            if self._childcare_levels(school, day):
                return school
        return self.env["bf.school"]

    def _childcare_company(self):
        """The company of the school whose child care this student attends today, else the card's
        own, else that of the latest active enrolment, else of the latest enrolment. Read in sudo.

        "Today" is the server's date here: this only picks a company, and the school's clock needs
        the company first.
        """
        self.ensure_one()
        student = self.sudo()
        school = student._childcare_school(fields.Date.today())
        if school:
            return school.company_id
        if student.company_id:
            return student.company_id
        Enrollment = self.env["bf.school.enrollment"].sudo()
        enrollment = Enrollment.search([("student_id", "=", student.id), ("state", "=", "active")],
                                       order="id desc", limit=1) \
            or Enrollment.search([("student_id", "=", student.id)], order="id desc", limit=1)
        return enrollment.school_id.company_id

    def _childcare_local_today(self):
        """Today on the clock of this student's school (its company's time zone), never the
        server's UTC: a date chosen on the portal after 8 p.m. was otherwise "in the past"."""
        self.ensure_one()
        company = self._childcare_company() or self.env.company
        return datetime.now(pytz.utc).astimezone(pytz.timezone(company.partner_id.tz or DEFAULT_TZ)).date()

    def _childcare_restrictions(self, day):
        """The pick-up restrictions of this student in force on `day`, read in sudo."""
        self.ensure_one()
        return self.env["bf.school.childcare.restriction"].sudo().search([
            ("student_id", "=", self.id),
            "|", ("date_from", "=", False), ("date_from", "<=", day),
            "|", ("date_to", "=", False), ("date_to", ">=", day)])


class SchoolCalendarDay(models.Model):
    _inherit = "bf.school.calendar.day"

    def _school_calendar_changed(self, before):
        """🔴 A day changed kind, date or school: a booking on a period that no longer runs that day
        (a class day turned pedagogical) would stay due, hold a place and vanish from the portal.
        It is closed, nothing due."""
        result = super()._school_calendar_changed(before)
        Booking = self.env["bf.school.childcare.booking"].sudo()
        for school, day in set(before) | {(d.school_id, d.date) for d in self}:
            Booking.search([("school_id", "=", school.id), ("date", "=", day), ("state", "=", "booked")]).filtered(
                lambda b: not b.slot_id._runs_on(b.date))._school_close()
        return result

    def _school_childcare_closed(self):
        """Child care closes that day (storm, closure): every booking is closed, nothing is due."""
        result = super()._school_childcare_closed()
        Booking = self.env["bf.school.childcare.booking"].sudo()
        for day in self:
            Booking.search([("school_id", "=", day.school_id.id), ("date", "=", day.date),
                            ("state", "=", "booked")])._school_close()
        return result
