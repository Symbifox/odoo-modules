import secrets
from datetime import date, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from . import ics

#: The age of majority in Québec (Civil Code, art. 153): from then on a student's timetable is theirs.
AGE_OF_MAJORITY = 18
#: What a calendar feed carries: a short look back and four months ahead.
LOOK_BACK_DAYS = 14
LOOK_AHEAD_DAYS = 120


class SchoolTimetableFeed(models.Model):
    """A private link to a minor student's timetable, for one adult's calendar program.

    The link is the secret: anyone holding it reads the timetable, nothing else, and the adult can
    replace it whenever they like. Each adult has their own, so replacing one stops no other, and a
    link stops by itself when the adult no longer receives the school's notices for the student, no
    longer has portal access, or the student comes of age: it is checked at every reading, not
    only when it is made. It names the student's classes by first name, never their address or
    anything else on the card.
    """

    _name = "bf.school.timetable.feed"
    _description = "Calendar feed of a student's timetable"
    _order = "student_id"

    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="cascade", index=True,
                                 domain=[("is_student", "=", True)])
    guardian_id = fields.Many2one("res.partner", "Adult", required=True, ondelete="cascade", index=True,
                                  domain=[("is_student", "=", False)])
    # 🔴 The card of a student seldom has a company: the link follows the school the student is enrolled in, so that
    # the office of another company never reads, nor revokes, a family's link.
    company_id = fields.Many2one(
        "res.company", compute="_compute_company_id", store=True, index=True, readonly=True)
    # 🔴 The token is the key to the timetable: not even the office reads it, only the family's own page does (sudo).
    token = fields.Char(required=True, index=True, copy=False, readonly=True, groups="base.group_system")
    created_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_read_on = fields.Datetime(readonly=True)

    _sql_constraints = [
        ("token_unique", "UNIQUE(token)", "A link is used once."),
        ("student_guardian_unique", "UNIQUE(student_id, guardian_id)", "An adult has one link at a time for a student."),
    ]

    @api.depends("student_id.company_id", "student_id.student_enrollment_ids.state",
                 "student_id.student_enrollment_ids.company_id")
    def _compute_company_id(self):
        for feed in self:
            student = feed.student_id.sudo()
            enrollments = student.student_enrollment_ids
            current = enrollments.filtered(lambda e: e.state == "active") or enrollments
            feed.company_id = (
                current[:1].company_id or student.company_id or feed.guardian_id.sudo().company_id
                or self.env.company)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("A calendar link is made from the family portal."))
        for vals in vals_list:
            vals["token"] = secrets.token_urlsafe(24)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("A calendar link is made from the family portal."))
        return super().write(vals)

    @api.model
    def _is_minor(self, student, today=None):
        """A student without a recorded birth date is taken for a minor, as the report card does."""
        birth = student.sudo().student_birthdate
        today = today or fields.Date.context_today(self)
        return not birth or relativedelta(today, birth).years < AGE_OF_MAJORITY

    @api.model
    def _first_name(self, student):
        return (student.sudo().name or "").split(" ")[0]

    @api.model
    def _rotate_for(self, student, guardian):
        """A new link for this adult and this student, the adult's old one stopping at once."""
        student, guardian = student.sudo(), guardian.sudo()
        self.sudo().search([("student_id", "=", student.id), ("guardian_id", "=", guardian.id)]).unlink()
        return self.sudo().create({"student_id": student.id, "guardian_id": guardian.id})

    def _may_be_read(self):
        """Whether the link still opens: the adult still receives this student's notices, the student is a minor."""
        self.ensure_one()
        guardian = self.guardian_id.sudo()
        # A link of the adult's own, to this student, with notices; and an adult who can still sign in.
        links = guardian._school_portal_links().filtered(
            lambda l: l.student_id == self.student_id and l.receives_notices)
        return bool(links and guardian.active and guardian.user_ids and self._is_minor(self.student_id))

    def _student_events(self, today=None):
        """The student's classes between a look back and a look ahead, as calendar events.

        Each date comes from the timetable in force on that date: a timetable that replaces another from the
        26th is read from the 26th, not from today.
        """
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        date_from, date_to = today - timedelta(days=LOOK_BACK_DAYS), today + timedelta(days=LOOK_AHEAD_DAYS)
        student = self.student_id.sudo()
        events = []
        enrollments = student.student_enrollment_ids.filtered(lambda e: e.state == "active")
        for enrollment in enrollments:
            school = enrollment.school_id
            for timetable, dates in self.env["bf.school.timetable"].sudo()._published_by_date(
                    school, date_from, date_to):
                tz = timetable._tz()
                slots = timetable.slot_ids.filtered(lambda s: s.group_id == enrollment.group_id)
                for item in timetable._occurrences(min(dates), max(dates), slots):
                    if item["date"] not in dates:
                        continue
                    start, end = (tz.localize(item[k]).astimezone(pytz.utc) for k in ("start", "end"))
                    room = item["room"].name if item["room"] else ""
                    events.append({
                        "uid": "%s-%s-%s@symbifox" % (student.id, item["slot"].id, item["date"]),
                        "start": start, "end": end, "summary": item["course"].subject_id.name,
                        "location": room,
                        "description": _("%(group)s, %(period)s, day %(day)s of the cycle",
                                         group=enrollment.group_id.name, period=item["period"].name,
                                         day=item["cycle_day"]),
                    })
        return events

    def _ics(self):
        self.ensure_one()
        name = _("%s: timetable", self._first_name(self.student_id))
        return ics.build_ics(name, self._student_events())
