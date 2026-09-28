import hashlib
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import consteq, html2plaintext


class SchoolFormType(models.Model):
    """A kind of authorisation: field trip, extracurricular activity..."""

    _name = "bf.school.form.type"
    _description = "Authorisation type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    requires_all_signers = fields.Boolean(
        "Every signing guardian must agree",
        help="Unticked, one guardian's authorisation is enough (Civil Code art. 603: towards "
             "a third party in good faith, one parent is presumed to act with the other's "
             "agreement). Ticked, the school waits for every guardian who signs.")
    default_body = fields.Html("Default text", translate=True, sanitize=True)
    active = fields.Boolean(default=True)


class SchoolForm(models.Model):
    """An authorisation asked of the families of some groups, for one event."""

    _name = "bf.school.form"
    _description = "Authorisation request"
    _inherit = ["mail.thread"]
    _order = "deadline desc, id desc"

    name = fields.Char(required=True, tracking=True)
    type_id = fields.Many2one("bf.school.form.type", "Type", required=True, ondelete="restrict")
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict")
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    group_ids = fields.Many2many(
        "bf.school.group", string="Groups", required=True,
        domain="[('school_id', '=', school_id), ('year_id.state', '=', 'current')]")
    body_html = fields.Html("Text", sanitize=True, required=True)
    event_date = fields.Date("Event date")
    deadline = fields.Date("Answer by", required=True, tracking=True)
    requires_all_signers = fields.Boolean(
        "Every signing guardian must agree", compute="_compute_requires_all_signers",
        store=True, readonly=False)
    state = fields.Selection(
        [("draft", "Draft"), ("sent", "Sent"), ("closed", "Closed")],
        default="draft", required=True, tracking=True, copy=False)
    sent_on = fields.Datetime(readonly=True, copy=False)
    body_hash = fields.Char(
        "Text fingerprint", readonly=True, copy=False,
        help="SHA-256 of the text as sent. Every answer keeps the fingerprint of the text "
             "it answered: the proof that nobody changed the text afterwards.")
    response_ids = fields.One2many("bf.school.form.response", "form_id", "Answers per student")
    count_accepted = fields.Integer("Authorised", compute="_compute_counts")
    count_refused = fields.Integer("Refused", compute="_compute_counts")
    count_pending = fields.Integer("Waiting", compute="_compute_counts")
    count_other = fields.Integer("No answer possible", compute="_compute_counts")

    @api.depends("type_id")
    def _compute_requires_all_signers(self):
        for form in self:
            form.requires_all_signers = form.type_id.requires_all_signers

    @api.onchange("type_id")
    def _onchange_type_body(self):
        if self.type_id.default_body and not html2plaintext(self.body_html or "").strip():
            self.body_html = self.type_id.default_body

    @api.depends("response_ids.state")
    def _compute_counts(self):
        for form in self:
            states = form.response_ids.mapped("state")
            form.count_accepted = states.count("accepted")
            form.count_refused = states.count("refused")
            form.count_pending = states.count("pending")
            form.count_other = len(states) - form.count_accepted - form.count_refused - form.count_pending

    def write(self, vals):
        # 🔴 The text a guardian answered cannot change under their answer.
        frozen = {"body_html", "name", "event_date", "group_ids", "requires_all_signers", "type_id"}
        if frozen & set(vals) and any(f.state != "draft" for f in self):
            raise UserError(_("A sent authorisation is not edited: its text is what the "
                              "families answered. Close it and send a new one."))
        return super().write(vals)

    def _fingerprint(self):
        self.ensure_one()
        text = "\n".join([self.name or "", str(self.event_date or ""),
                          html2plaintext(self.body_html or "").strip()])
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _students(self):
        """The students enrolled today in the chosen groups."""
        self.ensure_one()
        return self.env["bf.school.enrollment"].sudo().search([
            ("group_id", "in", self.group_ids.ids), ("state", "=", "active"),
            ("year_id.state", "=", "current")]).student_id

    def action_send(self):
        for form in self:
            if form.state != "draft":
                raise UserError(_("Only a draft authorisation is sent."))
            students = form._students()
            if not students:
                raise UserError(_("Nobody is enrolled in these groups."))
            responses = self.env["bf.school.form.response"].create([
                {"form_id": form.id, "student_id": s.id} for s in students])
            Answer = self.env["bf.school.form.answer"].sudo()
            for response in responses:
                signers = response.student_id.sudo().student_guardian_link_ids.filtered(
                    "can_sign").guardian_id
                Answer.create([{"response_id": response.id, "partner_id": p.id} for p in signers])
            form.write({"state": "sent", "sent_on": fields.Datetime.now(),
                        "body_hash": form._fingerprint()})
            sent, unreachable = responses.answer_ids._notify()
            no_signer = len(responses.filtered(lambda r: not r.answer_ids))
            body = _("Sent: %(students)s student(s), %(sent)s email(s) to guardians.",
                     students=len(responses), sent=sent)
            if unreachable:
                body += " " + _("%s signing guardian(s) have no email address: reach them another way.",
                                unreachable)
            if no_signer:
                body += " " + _("%s student(s) have no guardian who signs.", no_signer)
            form.message_post(body=body, message_type="notification",
                              subtype_xmlid="mail.mt_note")
        return True

    def action_remind(self):
        for form in self.filtered(lambda f: f.state == "sent"):
            pending = form.response_ids.filtered(lambda r: r.state == "pending").answer_ids.filtered(
                lambda a: a.decision == "pending")
            sent, _unreachable = pending._notify(reminder=True)
            form.message_post(body=_("Reminder emailed to %s guardian(s).", sent),
                              message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    def action_close(self):
        self.filtered(lambda f: f.state == "sent").write({"state": "closed"})
        return True

    @api.model
    def _cron_close_past_deadline(self):
        self.search([("state", "=", "sent"),
                     ("deadline", "<", fields.Date.context_today(self))]).action_close()


class SchoolFormResponse(models.Model):
    """The answer for one student: what the school may count on."""

    _name = "bf.school.form.response"
    _description = "Authorisation for a student"
    _order = "form_id, student_id"
    _rec_name = "student_id"

    form_id = fields.Many2one("bf.school.form", required=True, ondelete="cascade", index=True)
    student_id = fields.Many2one("res.partner", "Student", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="form_id.company_id", store=True)
    answer_ids = fields.One2many("bf.school.form.answer", "response_id", "Guardians' answers")
    state = fields.Selection(
        [("pending", "Waiting"), ("accepted", "Authorised"), ("refused", "Refused"),
         ("expired", "No answer"), ("no_signer", "No guardian signs")],
        compute="_compute_state", store=True, index=True)

    _sql_constraints = [
        ("form_student_unique", "UNIQUE(form_id, student_id)",
         "A student is asked once per authorisation."),
    ]

    @api.depends("answer_ids.decision", "form_id.state", "form_id.requires_all_signers")
    def _compute_state(self):
        """🔴 A refusal always wins: one parent's "no" keeps the child home, even if the
        other said yes. One "yes" is enough unless the type requires every signer."""
        for response in self:
            decisions = response.answer_ids.mapped("decision")
            if not decisions:
                response.state = "no_signer"
            elif "refused" in decisions:
                response.state = "refused"
            elif (all(d == "accepted" for d in decisions) if response.form_id.requires_all_signers
                  else "accepted" in decisions):
                response.state = "accepted"
            elif response.form_id.state == "closed":
                response.state = "expired"
            else:
                response.state = "pending"


class SchoolFormAnswer(models.Model):
    """One guardian's answer, with its evidence."""

    _name = "bf.school.form.answer"
    _description = "Guardian's answer to an authorisation"
    _order = "response_id, id"
    _rec_name = "partner_id"

    response_id = fields.Many2one("bf.school.form.response", required=True, ondelete="cascade", index=True)
    form_id = fields.Many2one(related="response_id.form_id", store=True, index=True)
    student_id = fields.Many2one(related="response_id.student_id", store=True)
    company_id = fields.Many2one(related="response_id.company_id", store=True)
    partner_id = fields.Many2one("res.partner", "Guardian", required=True, ondelete="restrict", index=True)
    decision = fields.Selection(
        [("pending", "Waiting"), ("accepted", "Authorised"), ("refused", "Refused")],
        default="pending", required=True)
    answered_on = fields.Datetime(readonly=True)
    answered_by_user_id = fields.Many2one("res.users", "Signed in as", readonly=True)
    channel = fields.Selection([("portal", "Family portal"), ("link", "Personal link")], readonly=True)
    ip_address = fields.Char("IP address", readonly=True)
    user_agent = fields.Char(readonly=True)
    body_hash = fields.Char("Fingerprint of the text answered", readonly=True)
    access_token = fields.Char(
        copy=False, readonly=True, default=lambda s: secrets.token_urlsafe(32))

    _sql_constraints = [
        ("response_partner_unique", "UNIQUE(response_id, partner_id)",
         "A guardian answers once per student."),
    ]

    def _check_token(self, token):
        self.ensure_one()
        return bool(token) and consteq(self.access_token or "", token)

    def _school_still_signs(self):
        """🔴 The link was sent while the adult signed: they answer only while they still do."""
        self.ensure_one()
        return bool(self.student_id.sudo().student_guardian_link_ids.filtered(
            lambda l: l.guardian_id == self.partner_id and l.can_sign))

    def _school_decide(self, decision, channel, ip=None, user_agent=None, answering_user=None):
        """Record a guardian's decision. Final: to change it, the family calls the school.

        🔴 The parameter is NOT named `user`: `_()` reads a frame-local `user` to pick
        the language, and fell on None (reference_odoo_gettext_reads_frame_local_user).
        """
        self.ensure_one()
        if decision not in ("accepted", "refused"):
            raise UserError(_("Unknown answer."))
        if self.decision != "pending":
            raise UserError(_("You have already answered. To change your answer, "
                              "contact the school office."))
        if self.form_id.state != "sent":
            raise UserError(_("This authorisation no longer takes answers."))
        if not self._school_still_signs():
            raise UserError(_("You no longer sign for this student."))
        self.sudo().write({
            "decision": decision, "answered_on": fields.Datetime.now(), "channel": channel,
            "ip_address": ip, "user_agent": (user_agent or "")[:500],
            "answered_by_user_id": (answering_user.id if answering_user
                                    and not answering_user._is_public() else False),
            "body_hash": self.form_id.body_hash,
        })
        self.form_id.sudo().message_post(
            body=_("%(guardian)s: %(decision)s for %(student)s.",
                   guardian=self.partner_id.name,
                   decision=dict(self._fields["decision"].selection)[decision],
                   student=self.student_id.name),
            message_type="notification", subtype_xmlid="mail.mt_note")
        return True

    def _url(self):
        self.ensure_one()
        return "/school/form/%s/%s" % (self.id, self.access_token)

    def _notify(self, reminder=False):
        """Email every guardian of these answers who has an address. Returns (sent, without email)."""
        template = self.env.ref("bf_school_forms.mail_template_school_form", raise_if_not_found=False)
        sent = unreachable = 0
        for answer in self.sudo():
            partner = answer.partner_id
            if not partner.email or not template:
                unreachable += 1
                continue
            lang = partner.lang or answer.form_id.company_id.partner_id.lang or "fr_CA"
            template.with_context(lang=lang, school_lang=lang, school_reminder=reminder,
                                  school_child=(answer.student_id.name or "").split(" ")[0]
                                  ).send_mail(
                answer.id, force_send=False,
                email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                email_values={"recipient_ids": [(6, 0, partner.ids)], "email_to": False})
            sent += 1
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()
        return sent, unreachable

    @api.model
    def _portal_answers_for(self, partner):
        """The answers this adult has to give or has given, newest first."""
        return self.sudo().search([("partner_id", "=", partner.id),
                                   ("form_id.state", "in", ("sent", "closed"))],
                                  order="id desc", limit=200)
