import os

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .homework import own_attachments

STATES = [("todo", "To hand in"), ("submitted", "Handed in"), ("returned", "Returned")]

#: What a student or a parent may hand in. Served back as downloads only, never inline.
ALLOWED_EXTENSIONS = frozenset({
    "pdf", "jpg", "jpeg", "png", "gif", "webp", "heic", "heif",
    "doc", "docx", "odt", "rtf", "txt", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp",
    "mp3", "m4a", "wav", "ogg", "mp4", "mov", "webm", "zip", "sb3",
})
MAX_FILES = 5
MAX_FILE_SIZE = 25 * 1024 * 1024


class Submission(models.Model):
    """One student's work for one homework: what they handed in and what came back."""

    _name = "bf.school.homework.submission"
    _description = "Hand-in"
    _inherit = ["mail.thread"]
    _order = "date_due desc, homework_id, student_id"
    _rec_names_search = ["student_id.name", "homework_id.name"]

    homework_id = fields.Many2one("bf.school.homework", "Homework", required=True,
                                  ondelete="cascade", index=True, readonly=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict",
                                 index=True, readonly=True, domain=[("is_student", "=", True)])
    group_id = fields.Many2one(related="homework_id.group_id", store=True, index=True)
    company_id = fields.Many2one(related="homework_id.company_id", store=True)
    teacher_id = fields.Many2one(related="homework_id.teacher_id")
    date_due = fields.Date(related="homework_id.date_due", store=True)
    submission_mode = fields.Selection(related="homework_id.submission_mode")
    points_total = fields.Float(related="homework_id.points_total")
    state = fields.Selection(STATES, default="todo", required=True, readonly=True, tracking=True)
    attachment_ids = fields.Many2many(
        "ir.attachment", "bf_school_submission_file_rel", "submission_id", "attachment_id",
        "Work handed in", readonly=True)
    submitted_on = fields.Datetime("Handed in on", readonly=True)
    submitted_by_id = fields.Many2one("res.partner", "Handed in by", readonly=True)
    is_late = fields.Boolean("Late", readonly=True,
                             help="Handed in after the end of the due date, at the school's time.")
    survey_answer_id = fields.Many2one("survey.user_input", "Quiz answer", readonly=True,
                                       ondelete="set null")
    grade = fields.Float("Mark", tracking=True)
    feedback = fields.Text("Comment")
    correction_ids = fields.Many2many(
        "ir.attachment", "bf_school_submission_correction_rel", "submission_id", "attachment_id",
        "Corrected copy")
    returned_on = fields.Datetime("Returned on", readonly=True)
    returned_by_id = fields.Many2one("res.users", "Returned by", readonly=True)

    _sql_constraints = [
        ("homework_student_unique", "UNIQUE(homework_id, student_id)",
         "A student hands in a homework once."),
    ]

    # 🔴 readonly= only guards the screen: by RPC a teacher could write a hand-in's date, its
    # files or its state. What the family did and when stays as the system recorded it; the
    # teacher writes the mark, the comment and the corrected copy, and the rest moves by the
    # buttons, which run in sudo after checking who presses them.
    _SYSTEM_FIELDS = frozenset({
        "homework_id", "student_id", "state", "attachment_ids", "submitted_on", "submitted_by_id",
        "is_late", "survey_answer_id", "returned_on", "returned_by_id"})

    @api.depends("student_id", "homework_id")
    def _compute_display_name(self):
        for submission in self:
            submission.display_name = "%s, %s" % (submission.student_id.name or "",
                                                  submission.homework_id.name or "")

    @api.constrains("grade", "homework_id")
    def _check_grade(self):
        for submission in self:
            if submission.grade < 0:
                raise ValidationError(_("A mark cannot be negative."))
            total = submission.homework_id.points_total
            if total and submission.grade > total:
                raise ValidationError(_("The mark of %(student)s is above the total (%(total)s).",
                                        student=submission.student_id.name, total=total))

    def write(self, vals):
        if not self.env.su and self._SYSTEM_FIELDS & set(vals):
            raise AccessError(_("A hand-in's files, dates and state are kept by the system."))
        res = super().write(vals)
        if "correction_ids" in vals:
            own_attachments(self, "correction_ids", self.env.uid)
        return res

    # ------------------------------------------------------------ the teacher
    def _check_teacher(self):
        """The group's teacher, or the office."""
        if self.env.su or self.env.user.has_group("bf_school_core.group_school_manager"):
            return
        for submission in self:
            if self.env.user not in submission.sudo().group_id.teacher_ids:
                raise AccessError(_("Only the teacher of the group returns this work."))

    def action_return(self):
        """Give the work back: the student and the parents now see the mark and the comment."""
        self._check_teacher()
        self.check_access("write")
        now = fields.Datetime.now()
        returned = self.filtered(lambda s: s.state != "returned").sudo()
        for submission in returned:
            submission.write({"state": "returned", "returned_on": now,
                              "returned_by_id": self.env.uid})
            submission._message_log(body=_("Returned by %s.", self.env.user.name))
        returned._school_notify_returned()
        return True

    def _school_notify_returned(self):
        """One email per person who chose to be told, in their language (see
        res.partner._school_work_return_recipients for who may)."""
        template = self.env.ref("bf_school_work.mail_template_work_returned", raise_if_not_found=False)
        if not template:
            return
        layout = self.env["bf.school"]._school_mail_layout()
        sent = False
        for submission in self.sudo():
            people = self.env["res.partner"]._school_work_return_recipients(submission)
            for person in people:
                lang = person.lang or submission.company_id.partner_id.lang or "fr_CA"
                template.with_context(lang=lang, school_lang=lang).send_mail(
                    submission.id, force_send=False, email_layout_xmlid=layout,
                    email_values={"recipient_ids": [(6, 0, person.ids)], "email_to": False})
            if people:
                sent = True
                submission._message_log(body=_("Told by email: %s.", ", ".join(people.mapped("name"))))
        if sent:
            self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    def action_reopen(self):
        """Take the work back from the family: the mark is hidden again and the files can be replaced."""
        self._check_teacher()
        self.check_access("write")
        for submission in self.filtered(lambda s: s.state == "returned").sudo():
            vals = {"returned_on": False, "returned_by_id": False,
                    "state": "submitted" if submission.submitted_on else "todo"}
            if submission.submission_mode == "survey":
                # A new attempt: the finished answer stays in the survey's results and in this log.
                vals.update({"state": "todo", "survey_answer_id": False, "submitted_on": False,
                             "submitted_by_id": False, "is_late": False, "grade": 0})
            submission.write(vals)
            submission._message_log(body=_("Reopened by %s.", self.env.user.name))
            # Returned then reopened before the mail queue ran: the mark must not leave (found
            # in review). The return email is a template email (not a chatter notification),
            # waiting or failed (an administrator could resend it).
            waiting = self.env["mail.mail"].sudo().search([
                ("model", "=", submission._name), ("res_id", "=", submission.id),
                ("is_notification", "=", False), ("state", "in", ("outgoing", "exception"))])
            if waiting:
                waiting.unlink()
                submission._message_log(body=_("Return email withdrawn: the work was reopened."))
        return True

    # ------------------------------------------------------------ the family
    def _school_is_enrolled(self):
        self.ensure_one()
        return bool(self.sudo().student_id.student_enrollment_ids.filtered(
            lambda e: e.group_id == self.homework_id.group_id and e.state == "active"))

    def _school_hand_in_refusal(self):
        """Why this work cannot be handed in now, or an empty string if it can."""
        self.ensure_one()
        submission = self.sudo()
        if submission.homework_id.submission_mode != "file":
            return _("This work is not handed in as files.")
        if submission.state == "returned":
            return _("The teacher has returned this work.")
        if not submission._school_is_enrolled():
            return _("The student is no longer in this group.")
        if (not submission.homework_id.accept_late
                and fields.Datetime.now() >= submission.homework_id._school_due_moment()):
            return _("The due date has passed and this work is not accepted late.")
        return ""

    @staticmethod
    def _school_file_refusal(name, size):
        extension = os.path.splitext(name or "")[1].lower().lstrip(".")
        if extension not in ALLOWED_EXTENSIONS:
            return _("%(name)s: this kind of file is not accepted (%(kinds)s).",
                     name=name, kinds=", ".join(sorted(ALLOWED_EXTENSIONS)))
        if size > MAX_FILE_SIZE:
            return _("%(name)s is larger than %(size)s MB.", name=name,
                     size=MAX_FILE_SIZE // (1024 * 1024))
        if not size:
            return _("%s is empty.", name)
        return ""

    def _school_hand_in(self, files, by_partner):
        """Replace what was handed in by these files: [(name, content)]. Runs for the portal."""
        self.ensure_one()
        refusal = self._school_hand_in_refusal()
        if not refusal and not files:
            refusal = _("Choose at least one file.")
        if not refusal and len(files) > MAX_FILES:
            refusal = _("At most %s files.", MAX_FILES)
        for name, content in files:
            refusal = refusal or self._school_file_refusal(name, len(content))
        if refusal:
            raise UserError(refusal)
        submission = self.sudo()
        old = submission.attachment_ids
        attachments = self.env["ir.attachment"].sudo().create([{
            "name": os.path.basename(name), "raw": content,
            "res_model": submission._name, "res_id": submission.id} for name, content in files])
        now = fields.Datetime.now()
        late = now >= submission.homework_id._school_due_moment()
        submission.write({
            "attachment_ids": [(6, 0, attachments.ids)], "state": "submitted",
            "submitted_on": now, "submitted_by_id": by_partner.id, "is_late": late})
        old.unlink()
        submission._message_log(body=_(
            "Handed in by %(who)s%(late)s: %(files)s", who=by_partner.name,
            late=_(" (late)") if late else "", files=", ".join(attachments.mapped("name"))))
        return True

    def _school_quiz_url(self, partner):
        """The link that opens this student's quiz for this person, the student or a parent.

        The survey refuses an answer opened by another person than its own (`answer_wrong_user`):
        an answer nobody started follows whoever opens it; one already started stays with
        whoever started it.
        """
        self.ensure_one()
        submission = self.sudo()
        homework = submission.homework_id
        if homework.submission_mode != "survey" or not homework.survey_id:
            raise UserError(_("This work is not an online quiz."))
        if submission.state == "returned":
            raise UserError(_("The teacher has returned this work."))
        if not submission._school_is_enrolled():
            raise UserError(_("The student is no longer in this group."))
        answer = submission.survey_answer_id
        if answer and answer.survey_id != homework.survey_id:
            answer = self.env["survey.user_input"]  # the quiz was replaced before anyone started
        if answer.state == "done":
            raise UserError(_("This quiz was already answered."))
        late = fields.Datetime.now() >= homework._school_due_moment()
        if late and not homework.accept_late:
            raise UserError(_("The due date has passed and this work is not accepted late."))
        if answer and answer.partner_id != partner:
            if answer.state != "new":
                raise UserError(_("%s has started this quiz: only they can finish it.",
                                  answer.partner_id.name))
            answer.write({"partner_id": partner.id, "email": partner.email,
                          "nickname": partner.name})
        if not answer:
            # Nobody follows the answer: the person answering would receive what is posted on it.
            answer = homework.survey_id.sudo().with_context(mail_create_nosubscribe=True)._create_answer(
                partner=partner, check_attempts=False)
            submission.survey_answer_id = answer
        answer.deadline = homework._school_quiz_deadline()
        return "/survey/start/%s?answer_token=%s" % (homework.survey_id.access_token,
                                                    answer.access_token)

    def _school_quiz_done(self, answer):
        """The survey is finished: it counts as handed in, and its score becomes the mark."""
        self.ensure_one()
        submission = self.sudo()
        if submission.state == "returned":
            return
        homework = submission.homework_id
        vals = {"state": "submitted", "submitted_on": answer.end_datetime,
                "submitted_by_id": answer.partner_id.id,
                "is_late": answer.end_datetime >= homework._school_due_moment()}
        if homework.points_total:
            vals["grade"] = round(answer.scoring_percentage * homework.points_total / 100, 2)
        submission.write(vals)
        submission._message_log(body=_("Quiz answered by %(who)s: %(score)s %%.",
                                       who=answer.partner_id.name, score=answer.scoring_percentage))
