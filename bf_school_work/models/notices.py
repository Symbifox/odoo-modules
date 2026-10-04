import logging
from datetime import timedelta

import pytz

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: How far ahead the daily summary looks for work to hand in, and how far back for work
#: still not handed in after its due date.
DIGEST_AHEAD_DAYS = 2
DIGEST_LATE_DAYS = 7


class School(models.Model):
    _inherit = "bf.school"

    work_offer_return_email = fields.Boolean(
        "Offer an email when work is returned",
        help="Each adult with parental authority, and each student with their own account, "
             "chooses on the portal whether to receive it. Nothing is sent until they do.")
    work_offer_digest = fields.Boolean(
        "Offer a daily summary",
        help="One email a day at most: work returned, work to hand in soon or late, new course "
             "contents. Each adult and each student chooses on the portal whether to receive it.")


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Chosen by the person on the portal (written in sudo by the portal page, which checks
    # that the person is the one choosing). Off by default: nothing is sent until ticked.
    # Administrators only otherwise: an employee who may edit contacts ticked a parent's
    # choice by RPC, or moved the summary's last date back (found in review).
    school_work_return_email = fields.Boolean("Email when work is returned", groups="base.group_system")
    school_work_digest = fields.Boolean("Daily summary of the work", groups="base.group_system")
    school_work_digest_sent = fields.Datetime(
        "Daily summary last sent", readonly=True, copy=False, groups="base.group_system")

    # --- Who gets what ------------------------------------------------------------

    def _school_work_acted_students(self):
        """The students this person may be emailed about: their children under parental
        authority, unless the school set the link to not receive notices (the same two
        conditions as every other school email; found in review), or themselves (a student's
        own account)."""
        self.ensure_one()
        students = self._school_portal_links().filtered(
            lambda l: l.has_parental_authority and l.receives_notices).student_id
        own = self.sudo()
        if own.is_student and own.active and own.user_ids.filtered("share"):
            students |= own
        return students.sudo()

    def _school_work_offers(self):
        """(return email offered, digest offered) by at least one school of these students."""
        self.ensure_one()
        schools = self._school_work_acted_students().student_enrollment_ids.filtered(
            lambda e: e.state == "active" and e.year_id.state == "current").school_id
        return any(schools.mapped("work_offer_return_email")), any(schools.mapped("work_offer_digest"))

    def _school_work_reachable(self):
        """Whether the work emails may go to this person's address: an active card with an
        email, an active portal account (where they chose, and where they can untick; found in
        review), and for a student, an address of their own. A student's card often carries a
        parent's address: the student's choice would send the marks to whoever reads it (found
        in review). Only an adult who may receive these emails for this student may share it."""
        self.ensure_one()
        person = self.sudo()
        if not (person.active and person.email and person.user_ids.filtered("share")):
            return False
        if not person.is_student:
            return True
        allowed = person.student_guardian_link_ids.filtered(
            lambda l: l.has_parental_authority and l.receives_notices).guardian_id
        sharing = person.search([("email_normalized", "=", person.email_normalized),
                                 ("id", "!=", person.id)])
        return bool(person.email_normalized) and not (sharing - allowed)

    @api.model
    def _school_work_return_recipients(self, submission):
        """The people told when this work is returned: they hold parental authority and receive
        the school's notices (or are the student, with their own account), their school offers
        it, they ticked it, and the email may go to their address. An adult who only receives
        notices never sees a mark, nor this email."""
        student = submission.student_id.sudo()
        if not submission.group_id.school_id.work_offer_return_email:
            return self.browse()
        people = student.student_guardian_link_ids.filtered(
            lambda l: l.has_parental_authority and l.receives_notices).guardian_id
        if student.user_ids.filtered("share"):
            people |= student
        return people.sudo().filtered(
            lambda p: p.school_work_return_email and p._school_work_reachable())

    # --- The daily summary ----------------------------------------------------------

    def _school_work_digest_sections(self, since, until=None):
        """[(student, {"returned": hand-ins, "due": homeworks, "late": homeworks,
        "contents": slides})] for what changed between `since` and `until` (now), per student,
        or [] if nothing. Bounded at `until`: a work returned while the summaries are being
        sent goes in tomorrow's, not in both (found in review)."""
        self.ensure_one()
        until = until or fields.Datetime.now()
        Submission = self.env["bf.school.homework.submission"].sudo()
        Homework = self.env["bf.school.homework"].sudo()
        sections = []
        for student in self._school_work_acted_students():
            enrolments = student.student_enrollment_ids.filtered(
                lambda e: e.state == "active" and e.year_id.state == "current"
                and e.school_id.work_offer_digest)
            groups = enrolments.group_id
            if not groups:
                continue
            # "Today" is the school's day, not the server's (the summary runs in the evening).
            tz = enrolments[:1].school_id._school_tz()
            today = pytz.utc.localize(until).astimezone(tz).date()
            returned = Submission.search([
                ("student_id", "=", student.id), ("group_id", "in", groups.ids),
                ("state", "=", "returned"), ("returned_on", ">", since), ("returned_on", "<=", until)])
            online = Homework.search([("group_id", "in", groups.ids), ("submission_mode", "!=", "none")])
            done = Submission.search([("homework_id", "in", online.ids), ("student_id", "=", student.id),
                                      ("state", "!=", "todo")]).homework_id
            pending = online - done
            due = pending.filtered(
                lambda h: today <= h.date_due <= today + timedelta(days=DIGEST_AHEAD_DAYS))
            late = pending.filtered(
                lambda h: today - timedelta(days=DIGEST_LATE_DAYS) <= h.date_due < today)
            contents = self._school_work_new_contents(student, since, until)
            if returned or due or late or contents:
                sections.append((student, {"returned": returned, "due": due.sorted("date_due"),
                                           "late": late.sorted("date_due"), "contents": contents}))
        return sections

    def _school_work_new_contents(self, student, since, until):
        """Course contents published since `since` in the school courses this person attends
        for this student (with bf_school_slides installed; nothing otherwise)."""
        self.ensure_one()
        if "slide.channel" not in self.env or "school_group_ids" not in self.env["slide.channel"]._fields:
            return []
        groups = student.student_enrollment_ids.filtered(lambda e: e.state == "active").group_id
        attended = self.env["slide.channel.partner"].sudo().search([
            ("partner_id", "=", self.id), ("member_status", "!=", "invited"),
            ("channel_id.school_group_ids", "in", groups.ids),
            ("channel_id.website_published", "=", True)]).channel_id
        return self.env["slide.slide"].sudo().search([
            ("channel_id", "in", attended.ids), ("is_published", "=", True),
            ("is_category", "=", False), ("date_published", ">", since),
            ("date_published", "<=", until)], order="date_published")

    @api.model
    def _cron_school_work_digest(self):
        """Once a day: one summary per person who ticked it, only when there is something."""
        template = self.env.ref("bf_school_work.mail_template_work_digest", raise_if_not_found=False)
        now = fields.Datetime.now()
        people = self.sudo().search([("school_work_digest", "=", True), ("email", "!=", False)])
        for person in people:
            since = person.school_work_digest_sent or now - timedelta(days=1)
            # Stamped even when nothing is sent: the next summary starts from here.
            person.school_work_digest_sent = now
            if not template or not person._school_work_reachable():
                continue
            # One person's summary that fails to render costs that person only.
            try:
                with self.env.cr.savepoint():
                    sections = person._school_work_digest_sections(since, now)
                    if not sections:
                        continue
                    lang = person.lang or "fr_CA"
                    template.with_context(lang=lang, school_lang=lang, school_digest=sections).send_mail(
                        person.id, force_send=False,
                        email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                        # Tied to no document: on the parent's card, the summary (every mark of
                        # every child) was in the chatter of a contact any employee may open,
                        # for as long as it waited to be sent, and for good if sending failed
                        # (found in review).
                        email_values={"recipient_ids": [(6, 0, person.ids)], "email_to": False,
                                      "model": False, "res_id": False})
            except Exception:
                _logger.exception("School work summary not sent to partner %s", person.id)
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
