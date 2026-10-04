from datetime import datetime, time, timedelta

import pytz
from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

SUBMISSION_MODES = [("none", "Not online"), ("file", "Files"), ("survey", "Online quiz")]


def own_attachments(records, field, by_uid):
    """The files linked in `field` are each record's own: uploaded for it, by this user.

    🔴 The portal serves these files in sudo to every family of the group. Without this
    check a teacher linked any attachment of the database (a signed document, another
    student's work) and the portal handed it out (found in review). A file uploaded on a
    record not yet saved carries res_id 0: it is attached to the record here.
    """
    for record in records:
        for attachment in record[field].sudo():
            if attachment.res_model != record._name or attachment.res_field:
                raise AccessError(_("%s was not uploaded here.", attachment.name))
            if attachment.res_id == record.id:
                continue
            if attachment.res_id or attachment.create_uid.id != by_uid:
                raise AccessError(_("%s was not uploaded here.", attachment.name))
            attachment.res_id = record.id


class School(models.Model):
    _inherit = "bf.school"

    def _school_tz(self):
        self.ensure_one()
        return pytz.timezone(self.company_id.partner_id.tz or "America/Toronto")

    def unlink(self):
        # The database cascade would drop groups, homework and hand-ins without the ORM.
        self.sudo().with_context(active_test=False).group_ids.unlink()
        return super().unlink()


class SchoolGroup(models.Model):
    _inherit = "bf.school.group"

    def unlink(self):
        self.env["bf.school.homework"].sudo().with_context(active_test=False).search(
            [("group_id", "in", self.ids)]).unlink()
        return super().unlink()


class Homework(models.Model):
    _inherit = "bf.school.homework"

    submission_mode = fields.Selection(
        SUBMISSION_MODES, "Hand-in mode", default="none", required=True,
        help="Not online: on paper or in class. Files: each student hands in files on the "
             "portal. Online quiz: each student answers a scored survey, whose score becomes "
             "the mark.")
    material_ids = fields.Many2many(
        "ir.attachment", "bf_school_homework_material_rel", "homework_id", "attachment_id",
        "Documents for the students",
        help="Served on the portal to the students of the group and to their parents.")
    points_total = fields.Float(
        "Marked out of", help="Leave at 0 for work that is not marked.")
    accept_late = fields.Boolean(
        "Accept late work", default=True,
        help="After the due date, work is still handed in and shown as late. Unticked, the "
             "portal refuses it.")
    survey_id = fields.Many2one(
        "survey.survey", "Quiz", domain=[("scoring_type", "!=", "no_scoring")],
        help="A survey with scoring. Each student answers it once, signed in; the score, out "
             "of the homework's total, becomes the mark. Its answers are read by the group's "
             "teachers and the office only.")
    submission_ids = fields.One2many("bf.school.homework.submission", "homework_id", "Hand-ins")
    submission_count = fields.Integer("Hand-ins expected", compute="_compute_submission_counts")
    submitted_count = fields.Integer("Handed in", compute="_compute_submission_counts")

    _sql_constraints = [
        ("points_total_positive", "CHECK(points_total >= 0)", "A total cannot be negative."),
    ]

    #: What the hand-ins were made for. Once a student has handed in, they do not change:
    #: moving the homework to another group carried the files of the first group's students
    #: to the other group's teacher (found in review).
    _SUBMISSION_KEYS = ("group_id", "submission_mode", "survey_id")

    @api.depends("submission_ids.state")
    def _compute_submission_counts(self):
        for homework in self:
            subs = homework.submission_ids
            homework.submission_count = len(subs)
            homework.submitted_count = len(subs.filtered(lambda s: s.state != "todo"))

    @api.constrains("submission_mode", "survey_id")
    def _check_survey(self):
        for homework in self:
            if homework.submission_mode == "survey" and not homework.survey_id:
                raise ValidationError(_("An online quiz needs its survey."))

    @api.constrains("points_total")
    def _check_points_total(self):
        for homework in self:
            top = max(homework.sudo().submission_ids.mapped("grade") or [0])
            if homework.points_total and top > homework.points_total:
                raise ValidationError(_("A mark already given (%(mark)s) is above this total.",
                                        mark=top))

    @api.model_create_multi
    def create(self, vals_list):
        homeworks = super().create(vals_list)
        own_attachments(homeworks, "material_ids", self.env.uid)
        homeworks._school_lock_surveys()
        homeworks._school_prepare_submissions()
        return homeworks

    def write(self, vals):
        moving = [key for key in self._SUBMISSION_KEYS if key in vals]
        if moving:
            self._school_check_change(vals, moving)
        res = super().write(vals)
        if "material_ids" in vals:
            own_attachments(self, "material_ids", self.env.uid)
        if moving:
            self._school_drop_strays()
            self._school_prepare_submissions()
        if {"survey_id", "group_id"} & set(vals):
            self._school_lock_surveys()
        if {"accept_late", "date_due"} & set(vals):
            self._school_sync_quiz_deadlines()
        return res

    def unlink(self):
        # 🔴 The database cascade (ondelete) would drop the hand-ins without the ORM: their
        # files and their log stayed behind, readable by any employee who guessed an id
        # (found in review). Through the ORM, both go with them.
        self.sudo().submission_ids.unlink()
        return super().unlink()

    def _school_check_change(self, vals, keys):
        # A teacher moving a homework to a group they do not teach: refused by bf_school_homework.
        for homework in self:
            started = homework.sudo().submission_ids.filtered(
                lambda s: s.state != "todo" or s.survey_answer_id)
            if not started:
                continue
            for key in keys:
                current = homework[key]
                current = current.id if isinstance(current, models.BaseModel) else current
                if current != (vals[key] or False):
                    raise UserError(_(
                        "%(students)s student(s) already handed in %(homework)s: its group, how "
                        "it is handed in and its quiz no longer change. Create another homework.",
                        students=len(started), homework=homework.name))

    def _school_drop_strays(self):
        """Hand-ins nobody started that no longer belong: work no longer online, students
        outside the group."""
        for homework in self.sudo():
            enrolled = homework.group_id._active_students()
            homework.submission_ids.filtered(
                lambda s: s.state == "todo" and not s.survey_answer_id and (
                    homework.submission_mode == "none" or s.student_id not in enrolled)).unlink()

    def _school_lock_surveys(self):
        """A homework's quiz: by invitation, signed in, once, read by the group's teachers.

        🔴 The Surveys user role reads and writes the answers of every survey that is not
        restricted to some users: any staff member read a 301 student's answers and could
        change the quiz (found in review). Restricting the survey to the group's teachers and
        the office closes it. One attempt by invitation also makes a "retry" share the
        invitation of the first answer, which `survey.user_input._mark_done` follows.
        """
        office = self.env.ref("bf_school_core.group_school_manager").sudo().users
        for homework in self.sudo().filtered("survey_id"):
            survey = homework.survey_id
            readers = (homework.group_id.teacher_ids | homework.teacher_id | survey.user_id
                       | survey.create_uid | office).filtered(lambda u: not u.share)
            survey.write({
                "access_mode": "token", "users_login_required": True,
                "is_attempts_limited": True, "attempts_limit": 1,
                "restrict_user_ids": [(4, user.id) for user in readers],
            })

    def _school_sync_quiz_deadlines(self):
        """The quiz closes at the due moment unless late work is accepted, also for answers
        already opened."""
        for homework in self.sudo().filtered(lambda h: h.submission_mode == "survey"):
            answers = homework.submission_ids.survey_answer_id.filtered(lambda a: a.state != "done")
            answers.write({"deadline": homework._school_quiz_deadline()})

    def _school_quiz_deadline(self):
        self.ensure_one()
        return False if self.accept_late else self._school_due_moment()

    def _school_due_moment(self):
        """The moment work becomes late: the end of the due date at the school's time, in UTC."""
        self.ensure_one()
        tz = self.sudo().group_id.school_id._school_tz()
        end = tz.localize(datetime.combine(self.date_due + timedelta(days=1), time.min))
        return end.astimezone(pytz.utc).replace(tzinfo=None)

    def _school_prepare_submissions(self):
        """One hand-in per student enrolled in the group, for work handed in online.

        Called after the homework itself was written under the user's rights: the hand-ins
        are the system's records, created in sudo. A student who joins the group later gets
        theirs on first need (`_school_submission_for`).
        """
        for homework in self.filtered(lambda h: h.submission_mode != "none"):
            for student in homework.sudo().group_id._active_students():
                homework._school_submission_for(student)

    def _school_submission_for(self, student):
        """This student's hand-in, created the first time it is needed."""
        self.ensure_one()
        # Nobody follows a hand-in: whoever created it first (a parent, from the portal) would
        # otherwise receive the messages posted on it.
        Submission = self.env["bf.school.homework.submission"].sudo().with_context(
            mail_create_nosubscribe=True)
        domain = [("homework_id", "=", self.id), ("student_id", "=", student.id)]
        submission = Submission.search(domain, limit=1)
        if not submission:
            try:
                with self.env.cr.savepoint():
                    submission = Submission.create({"homework_id": self.id, "student_id": student.id})
            except IntegrityError:
                # Another request created it at the same moment; this transaction cannot see it
                # (repeatable read): the family opens the page again.
                raise UserError(_("This work changed at the same moment: open it again.")) from None
        return submission

    def action_school_open_submissions(self):
        self.ensure_one()
        self._school_prepare_submissions()
        return {
            "type": "ir.actions.act_window",
            "name": _("Hand-ins: %s", self.name),
            "res_model": "bf.school.homework.submission",
            "view_mode": "list,form",
            "domain": [("homework_id", "=", self.id)],
            "context": {"create": False},
        }

    @api.model
    def _school_for_partner(self, partner, since=None):
        """A student's own account sees their own homework, like a parent sees a child's."""
        pairs = super()._school_for_partner(partner, since=since)
        student = partner.sudo()
        if not (student.is_student and student.active):
            return pairs
        groups = student.student_enrollment_ids.filtered(
            lambda e: e.state == "active" and e.year_id.state == "current").group_id
        if not groups:
            return pairs
        domain = [("group_id", "in", groups.ids)]
        if since:
            domain.append(("date_due", ">=", since))
        own = [(homework, student) for homework in self.sudo().search(domain, order="date_due, id")]
        return sorted(pairs + own, key=lambda p: (p[0].date_due, p[0].id))
