from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .report_card import AGE_OF_MAJORITY, MANAGER

#: Written by the buttons only.
SYSTEM_FIELDS = {"state", "sent_on", "sent_by_id", "channel", "recipient_ids"}


def _forges(value):
    """Does this value of a system field say something? A list of commands that sets nothing forges nothing."""
    if isinstance(value, (list, tuple)):
        return any(not isinstance(c, (list, tuple)) or c[0] not in (5, 6) or (c[0] == 6 and c[2]) for c in value)
    return bool(value)


class SchoolCommunication(models.Model):
    """A written communication to the parents that is not a report card.

    Education Regulation (I-13.3, r. 8): s. 29, a first written communication on the learning and
    behaviour of each student at the latest on October 15; s. 29.2, information at least once a
    month to the parents of a minor student whose results may not reach the success threshold,
    whose behaviour breaks the school's conduct rules, or when the intervention plan says so.
    Either goes to the student themself if they are of age.
    """

    _name = "bf.school.communication"
    _description = "Written communication to the parents"
    _inherit = ["bf.school.staff.thread"]
    _order = "due_date, student_id, id"

    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True, tracking=True,
        domain=[("is_student", "=", True)])
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="year_id.school_id", store=True)
    company_id = fields.Many2one(related="year_id.company_id", store=True)
    kind = fields.Selection(
        [("first", "First written communication"), ("monthly", "Monthly information"), ("other", "Other")],
        required=True, default="monthly", tracking=True)
    reason = fields.Selection(
        [("performance", "Results that may not reach the success threshold"),
         ("conduct", "Behaviour that breaks the conduct rules"),
         ("plan", "Provided in the intervention plan")],
        help="Monthly information only (Education Regulation s. 29.2).")
    due_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    subject = fields.Char("Subject of the message", required=True, default=lambda self: _("A message from the school"))
    body = fields.Text(help="What the parents are told. Written by the teacher or the office.")
    state = fields.Selection([("to_send", "To send"), ("sent", "Sent")], default="to_send", required=True,
                             tracking=True, copy=False)
    sent_on = fields.Datetime(copy=False)
    sent_by_id = fields.Many2one("res.users", copy=False)
    channel = fields.Selection([("email", "Email"), ("other", "On paper or in person")], copy=False)
    recipient_ids = fields.Many2many(
        "res.partner", "bf_school_communication_recipient_rel", "communication_id", "partner_id", "Told by email",
        copy=False, readonly=True,
        help="The adults the email was sent to. The email itself is not kept once it is sent: this is the trace "
             "of who was told, never of what was said.")
    is_overdue = fields.Boolean(compute="_compute_is_overdue")

    @api.depends("state", "due_date")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for item in self:
            item.is_overdue = bool(item.state == "to_send" and item.due_date and today > item.due_date)

    @api.constrains("subject")
    def _check_subject(self):
        for item in self:
            if item.subject and ("\n" in item.subject or "\r" in item.subject):
                # 🔴 A line break in the subject of an email makes the email impossible to send, and the
                # communication would stay "sent".
                raise ValidationError(_("The subject of the message is one line."))

    @api.constrains("kind", "reason")
    def _check_reason(self):
        for item in self:
            if item.kind == "monthly" and not item.reason:
                raise ValidationError(_("Monthly information says which of the three cases of s. 29.2 it answers."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                # An empty value forges nothing: the web client sends the screen's defaults as they are.
                forbidden = {f for f in (set(vals) & SYSTEM_FIELDS) - {"state"} if _forges(vals[f])}
                if forbidden or vals.get("state", "to_send") != "to_send":
                    raise AccessError(_("A communication is marked sent with its button."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if set(vals) & SYSTEM_FIELDS:
                raise AccessError(_("A communication is marked sent with its button."))
            # 🔴 A communication stays with its student and year: moving it would send a message written for one
            # family to the parents of another child (and `school_id` and `company_id` are related to the year).
            if {"student_id", "year_id", "school_id", "company_id"} & set(vals):
                raise UserError(_("A communication stays with its student and its school year."))
            if any(item.state == "sent" for item in self):
                raise UserError(_("A communication that was sent is kept as it went."))
        return super().write(vals)

    def unlink(self):
        if any(item.state == "sent" for item in self):
            raise UserError(_("A communication that was sent is kept as it went."))
        return super().unlink()

    # --- Preparing them ---------------------------------------------------------------------------

    @api.model
    def _school_year_as_user(self, year):
        """The year, read as the user first (the rule of the company is the user's), then as the superuser."""
        year = year.with_env(self.env)
        year.check_access("read")
        return year.sudo()

    @api.model
    def _prepare_first(self, year):
        """One first communication to prepare per student enrolled this year, due October 15."""
        year = self._school_year_as_user(year)
        students = self.env["bf.school.enrollment"].sudo().search(
            [("year_id", "=", year.id), ("state", "=", "active")]).student_id
        have = self.sudo().search([("year_id", "=", year.id), ("kind", "=", "first")]).student_id
        created = self.sudo().browse()
        for student in students - have:
            created |= self.sudo().create({
                "student_id": student.id, "year_id": year.id, "kind": "first",
                "due_date": year.first_communication_date, "subject": _("First written communication")})
        return created

    @api.model
    def _prepare_monthly_performance(self, year, today=None):
        """Students whose latest term result is below the threshold, with no information sent this month.

        An aid: the teacher still decides what to say, and the conduct and plan cases are entered by hand.
        """
        year = self._school_year_as_user(year)
        today = today or fields.Date.context_today(self)
        month_start = today.replace(day=1)
        threshold = year.success_threshold
        Result = self.env["bf.school.course.result"].sudo()
        # Where each student stands in each course: the latest term that has a result, not the latest term of the year
        # (a term nobody has graded yet must not hide a student who is failing the one before).
        latest = {}
        for result in Result.search([("course_id.year_id", "=", year.id), ("has_percent", "=", True)]).sorted(
                lambda r: r.term_id.sequence):
            if result.student_id in result.course_id._students():
                latest[(result.student_id.id, result.course_id.id)] = result
        failing = Result.browse([r.id for r in latest.values() if r.percent < threshold])
        if not failing:
            return self.sudo().browse()
        done = self.sudo().search([
            ("year_id", "=", year.id), ("kind", "=", "monthly"), ("reason", "=", "performance"),
            ("due_date", ">=", month_start), ("due_date", "<", month_start + relativedelta(months=1))]).student_id
        created = self.sudo().browse()
        for student in failing.student_id - done:
            subjects = failing.filtered(lambda r, s=student: r.student_id == s).course_id.subject_id.mapped("name")
            created |= self.sudo().create({
                "student_id": student.id, "year_id": year.id, "kind": "monthly", "reason": "performance",
                "due_date": today, "subject": _("Where %s stands", student.name),
                "body": _("The results in %(subjects)s may not reach the success threshold of %(threshold)s %%.",
                          subjects=", ".join(subjects), threshold=int(threshold))})
        return created

    # --- Sending ---------------------------------------------------------------------------------

    def _recipients(self):
        self.ensure_one()
        student = self.student_id.sudo()
        birth = student.student_birthdate
        if birth and relativedelta(fields.Date.context_today(self), birth).years >= AGE_OF_MAJORITY:
            return student
        return student.student_guardian_link_ids.filtered("has_parental_authority").guardian_id

    def action_send_email(self):
        if not self.env.user.has_group("bf_school_core.group_school_user"):
            raise AccessError(_("Only the school staff send communications."))
        self.check_access("write")
        template = self.env.ref("bf_school_report_card.mail_template_communication")
        for item in self.sudo():
            if item.state == "sent":
                continue
            if not item.body:
                raise UserError(_("Write what the parents are told before sending it."))
            adults = item._recipients().filtered("email")
            if not adults:
                raise UserError(_("No adult with an email to tell for %s: send it another way and mark it sent.",
                                  item.student_id.name))
            if "\n" in (item.student_id.name or "") or "\r" in (item.student_id.name or ""):
                # The subject of the email is the student's name and the subject of the message.
                raise UserError(_("The name of the student has a line break: the email cannot be sent."))
            told = self.env["res.partner"]
            for adult in adults:
                lang = adult.lang or self.env.company.partner_id.lang or "fr_CA"
                queued = template.with_context(lang=lang, school_lang=lang).send_mail(
                    item.id, force_send=False, email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
                if not queued:
                    raise UserError(_("The email for %s was not queued: the communication stays to send.", adult.name))
                told |= adult
            # 🔴 "Sent" only once every email is in the queue, and the trace of who was told stays: the email is
            # deleted when it is sent, the thread keeps the names and never the text.
            item.write({"state": "sent", "sent_on": fields.Datetime.now(), "sent_by_id": self.env.user.id,
                        "channel": "email", "recipient_ids": [(6, 0, told.ids)]})
            item.message_post(body=_("Sent by email to: %s.", ", ".join(told.mapped("name"))))
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        return True

    def action_mark_sent(self):
        if not self.env.user.has_group("bf_school_core.group_school_user"):
            raise AccessError(_("Only the school staff send communications."))
        self.check_access("write")
        for item in self.sudo().filtered(lambda i: i.state == "to_send"):
            item.write({"state": "sent", "sent_on": fields.Datetime.now(), "sent_by_id": self.env.user.id,
                        "channel": "other"})
        return True


class SchoolYear(models.Model):
    _inherit = "bf.school.year"

    def action_prepare_first_communications(self):
        """One draft first communication per enrolled student, due October 15."""
        self.ensure_one()
        self._check_communications_office()
        created = self.env["bf.school.communication"]._prepare_first(self)
        return self._communications_action(_("First written communications"), created)

    def action_prepare_monthly_information(self):
        """Information to give this month to the parents of students whose results fall below the threshold."""
        self.ensure_one()
        self._check_communications_office()
        created = self.env["bf.school.communication"]._prepare_monthly_performance(self)
        return self._communications_action(_("Monthly information to prepare"), created)

    def _check_communications_office(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration prepares the communications of a year."))

    def _communications_action(self, name, records):
        return {
            "type": "ir.actions.act_window", "name": name, "res_model": "bf.school.communication",
            "view_mode": "list,form", "domain": [("year_id", "=", self.id), ("state", "=", "to_send")],
            "context": {"default_year_id": self.id}}
