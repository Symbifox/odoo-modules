import base64
import hashlib
import json
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

MANAGER = "bf_school_core.group_school_manager"

#: A student of age receives their own report cards: the regulation sends them to the parents
#: "toutefois, s'il est majeur, c'est à l'élève" (I-13.3, r. 8, s. 29.1).
AGE_OF_MAJORITY = 18

#: Written by the system only: by a button or by the generation, never by an RPC write.
SYSTEM_FIELDS = {
    "state", "version", "supersedes_id", "replaced_by_id", "issued_on", "issued_by_id", "issued_late",
    "snapshot", "snapshot_sha256", "pdf", "pdf_name", "pdf_error", "pdf_sha256", "published_late", "published_on",
    "correction_reason"}
#: What a homeroom teacher or the office writes on a draft.
WRITING_FIELDS = {"comment", "decision", "decision_note"}


class SchoolReportCard(models.Model):
    """A student's report card for one term.

    The results are the gradebook's, copied onto the card when it is generated and refreshed
    until it is issued; nobody types a figure. Issuing freezes the card: its content is sealed
    in a JSON snapshot with its SHA-256, filed in the student's file, and a correction is a new
    version that replaces it, never an edit.

    Covers the elementary and secondary report cards (I-13.3, r. 8, annexes V to VII, s. 30.1
    and 30.2). The preschool report card (annex IV) gives statements, not percentages, and is
    not covered.
    """

    _name = "bf.school.report.card"
    _description = "Report card"
    _inherit = ["bf.school.staff.thread"]
    _order = "year_id desc, term_id, group_id, student_id, version desc"

    #: What `ir.attachment` does not change from outside: the file under this field (see `bf_school_record`).
    _school_sealed_fields = ("pdf",)

    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True,
        domain=[("is_student", "=", True)])
    group_id = fields.Many2one("bf.school.group", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="group_id.school_id", store=True, index=True)
    year_id = fields.Many2one(related="group_id.year_id", store=True, index=True)
    company_id = fields.Many2one(related="group_id.company_id", store=True)
    term_id = fields.Many2one("bf.school.term", "Term", required=True, ondelete="restrict", index=True,
                              domain="[('year_id', '=', year_id)]")
    level_id = fields.Many2one(
        "bf.school.level", "Level", ondelete="restrict",
        help="The student's level this year, taken from the enrolment when it has one, else from "
             "the group when the group has a single level.")
    stage = fields.Selection(related="level_id.stage", store=True)
    lang = fields.Selection(
        lambda self: self.env["res.lang"].get_installed(), "Language", required=True,
        default=lambda self: self.env.company.partner_id.lang or "fr_CA",
        help="The language the report card is written in: subject names and the page.")
    kind = fields.Selection(
        [("interim", "Term report card"), ("final", "Last report card of the year")],
        compute="_compute_kind", store=True)
    state = fields.Selection(
        [("draft", "Draft"), ("issued", "Issued"), ("superseded", "Replaced")],
        default="draft", required=True, tracking=True, copy=False)
    version = fields.Integer(default=1, copy=False)
    supersedes_id = fields.Many2one("bf.school.report.card", copy=False, ondelete="restrict")
    replaced_by_id = fields.Many2one("bf.school.report.card", copy=False, ondelete="restrict")
    correction_reason = fields.Char(copy=False)
    line_ids = fields.One2many("bf.school.report.card.line", "card_id", "Subjects")
    comment = fields.Text(
        "Comment", help="The homeroom teacher's general comment, printed on the report card.")
    decision = fields.Selection(
        [("promoted", "Moves on to the next level"), ("repeats", "Repeats the level"),
         ("other", "Other, see the note")], string="Year-end decision",
        help="Last report card of the year only. The school's pass rules decide (Education "
             "Regulation s. 28); the card records the decision, with the subjects not passed shown.")
    decision_note = fields.Text("Decision note")
    deadline = fields.Date(related="term_id.report_deadline")
    issued_on = fields.Datetime(copy=False, tracking=True)
    issued_by_id = fields.Many2one("res.users", copy=False)
    issued_late = fields.Boolean(copy=False, help="Issued after the term's deadline.")
    is_overdue = fields.Boolean("Overdue", compute="_compute_is_overdue")
    snapshot = fields.Text(copy=False, groups=MANAGER)
    snapshot_sha256 = fields.Char("SHA-256", copy=False)
    pdf = fields.Binary(attachment=True, copy=False, groups=MANAGER)
    pdf_name = fields.Char(copy=False)
    pdf_sha256 = fields.Char("PDF SHA-256", copy=False, readonly=True)
    published_late = fields.Boolean("Shown late", copy=False, help="Shown to the families after the term's deadline: the deadline is "
                                                    "about the families receiving the report card.")
    pdf_error = fields.Char("PDF error", copy=False, readonly=True,
                            help="Why the last rendering failed. The cron leaves a report card with an error alone; the office retries it.")
    published_on = fields.Datetime("Shown to the families on", copy=False, tracking=True)
    read_ids = fields.One2many("bf.school.report.card.read", "card_id", "Read by")
    recipient = fields.Selection(
        [("guardians", "The parents"), ("student", "The student (of age)")],
        compute="_compute_recipient", string="Sent to")

    _sql_constraints = [
        ("student_term_version_unique", "UNIQUE(student_id, term_id, version)",
         "A student has one report card per term and version."),
    ]

    @api.depends("term_id.sequence")
    def _compute_kind(self):
        for card in self:
            card.kind = "final" if card.term_id.sequence == 3 else "interim"

    @api.depends("state", "deadline", "published_on", "version")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for card in self:
            # The deadline is for the families to have the report card: a draft or an issued one not yet shown,
            # past it, is overdue. A corrected version replaces one already sent: it is not.
            card.is_overdue = bool(card.version == 1 and card.state in ("draft", "issued") and not card.published_on
                                   and card.deadline and today > card.deadline)

    @api.depends("student_id.student_birthdate")
    def _compute_recipient(self):
        today = fields.Date.context_today(self)
        for card in self:
            birth = card.student_id.sudo().student_birthdate
            of_age = bool(birth and relativedelta(today, birth).years >= AGE_OF_MAJORITY)
            card.recipient = "student" if of_age else "guardians"

    @api.depends_context("lang")
    @api.depends("student_id.name", "term_id.sequence", "version")
    def _compute_display_name(self):
        for card in self:
            name = _("%(student)s, term %(term)s", student=card.student_id.name or "", term=card.term_id.sequence)
            card.display_name = name if card.version == 1 else "%s (v%s)" % (name, card.version)

    @api.constrains("term_id", "group_id")
    def _check_term(self):
        for card in self:
            if card.term_id.year_id != card.group_id.year_id:
                raise ValidationError(_("The term belongs to another school year than the group."))

    @api.constrains("decision", "term_id")
    def _check_decision(self):
        for card in self.filtered("decision"):
            if card.term_id.sequence != 3:
                raise ValidationError(_("The year-end decision goes on the last report card of the year."))

    # --- Guards: what moves by a button, what a teacher writes ---------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if not self.env.user.has_group(MANAGER):
                raise AccessError(_("Report cards are generated by the school administration."))
            for vals in vals_list:
                # An empty value forges nothing: the web client sends the screen's defaults as they are.
                forbidden = {f for f in (set(vals) & SYSTEM_FIELDS) - {"state", "version"} if vals[f]}
                if forbidden or vals.get("state", "draft") != "draft" or vals.get("version", 1) != 1:
                    raise AccessError(_("A report card's state and proof move with the buttons."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if set(vals) & SYSTEM_FIELDS:
                # 🔴 `readonly` is no guard against RPC: state, seal and publication are the
                # buttons' alone.
                raise AccessError(_("A report card's state and proof move with the buttons."))
            if any(card.state != "draft" for card in self):
                raise UserError(_("An issued report card is frozen: a correction is a new version."))
            if not self.env.user.has_group(MANAGER) and set(vals) - WRITING_FIELDS:
                raise AccessError(_("A teacher writes the comment and the decision of a report card."))
            if {"student_id", "group_id", "term_id"} & set(vals):
                raise UserError(_("A report card stays on its student, group and term."))
        return super().write(vals)

    def unlink(self):
        if any(card.state != "draft" or card.version > 1 for card in self):
            raise UserError(_("An issued report card is kept: it is the school's record of what was sent."))
        return super().unlink()

    # --- Building the card from the gradebook -----------------------------------------------------

    @api.model
    def _student_level(self, student, group):
        """The student's level: the enrolment's when the installed suite records one, else the group's single one."""
        enrollment = group.enrollment_ids.filtered(lambda e: e.student_id == student)[:1]
        if "level_id" in enrollment._fields and enrollment.level_id:
            return enrollment.level_id
        return group.level_ids if len(group.level_ids) == 1 else self.env["bf.school.level"]

    @api.model
    def _generate(self, group, term, lang=None):
        """A draft report card for each active student of the group, from the gradebook.

        Existing drafts are refreshed, keeping the teachers' comments. Issued ones are left alone.
        """
        # 🔴 The group and the term are read as the user first: the company's rule is the user's, not the superuser's.
        group, term = group.with_env(self.env), term.with_env(self.env)
        group.check_access("read")
        term.check_access("read")
        group, term = group.sudo(), term.sudo()
        if term.year_id != group.year_id:
            raise UserError(_("The term belongs to another school year than the group."))
        if group.level_ids and all(level.stage == "preschool" for level in group.level_ids):
            raise UserError(_(
                "%s is a preschool group: its report card gives statements on the development of "
                "competencies, not percentages (annex IV), and is not made here.", group.name))
        cards = self.sudo().browse()
        cache = {}
        if term.sequence == 3:
            cache = {c.id: c._compute_finals() for c in self.env["bf.school.course"].sudo().search(
                [("group_id", "=", group.id)])}
        for student in group._active_students():
            card = self.sudo().search([
                ("student_id", "=", student.id), ("term_id", "=", term.id), ("state", "!=", "superseded")],
                order="version desc", limit=1)
            if card and card.state == "issued":
                continue
            if not card:
                level = self._student_level(student, group)
                card = self.sudo().create({
                    "student_id": student.id, "group_id": group.id, "term_id": term.id, "level_id": level.id or False})
            if lang and card.lang != lang:
                card.lang = lang
            if card.group_id != group:
                card.group_id = group  # the student changed group: one report card per term, in the group they are in
            card._refresh_lines(cache)
            cards |= card
        return cards

    def _refresh_lines(self, finals_cache=None):
        """Rewrite the subject lines from the gradebook; comments are kept, by course."""
        for card in self.sudo():
            if card.state != "draft":
                raise UserError(_("An issued report card is not refreshed."))
            kept = {line.course_id.id: line.comment for line in card.line_ids if line.comment}
            card.line_ids.unlink()
            for vals in card._line_vals(finals_cache):
                vals["comment"] = kept.get(vals["course_id"])
                self.env["bf.school.report.card.line"].sudo().create(dict(vals, card_id=card.id))
        return True

    def _line_vals(self, finals_cache=None):
        """The lines and component rows of this card, read from the gradebook."""
        self.ensure_one()
        student, term = self.student_id, self.term_id
        Result = self.env["bf.school.course.result"].sudo()
        # The courses of the group the student is in now, and those of a group they left this year where they
        # have results: a student who changes group keeps what was graded before.
        courses = self.env["bf.school.course"].sudo().search([("group_id", "=", self.group_id.id)])
        courses |= Result.search([("student_id", "=", student.id), ("course_id.year_id", "=", self.year_id.id),
                                  ("term_id.sequence", "<=", term.sequence)]).course_id
        out = []
        for sequence, course in enumerate(courses.sorted(lambda c: (c.subject_id.sequence, c.id)), start=1):
            course = course.with_context(lang=self.lang)
            result = Result.search([("course_id", "=", course.id), ("term_id", "=", term.id),
                                    ("student_id", "=", student.id)])
            vals = {
                "sequence": sequence, "course_id": course.id, "subject_name": course.subject_id.name,
                "teacher_names": ", ".join(course.teacher_ids.mapped("name")), "mode": course.result_mode,
                "units": course.units if self.stage == "secondary" else 0,
                "has_percent": bool(result and result.has_percent),
                "percent": result.percent if result and result.has_percent else 0,
                "group_average": result.group_average if result and result.has_percent else 0,
                "weights_ok": course.weights_ok,
            }
            components = []
            result_by_component = {l.component_id.id: l.percent for l in result.line_ids}
            final = None
            if self.kind == "final":
                finals = (finals_cache or {}).get(course.id)
                final = (finals if finals is not None else course._compute_finals()).get(student.id)
            for component in course.component_ids.sorted("sequence"):
                shown = result_by_component.get(component.id)
                final_value = final["components"].get(component.id) if final else None
                if shown is None and final_value is None:
                    continue
                components.append((0, 0, {
                    "sequence": component.sequence, "name": component.component_id.with_context(lang=self.lang).name,
                    "has_percent": shown is not None, "percent": shown or 0,
                    "has_final": final_value is not None, "final_percent": final_value or 0}))
            vals["component_ids"] = components
            if final:
                vals.update({
                    "has_final": final["final"] is not None, "final_percent": final["final"] or 0,
                    "provisional": final["provisional"],
                    "has_exam": final["exam"] is not None, "exam_percent": final["exam"] or 0,
                    "final_group_average": final["group_average"] or 0,
                    "outcome": ("passed" if final["passed"] else "failed") if final["passed"] is not None else False,
                    "units_earned": final["units"] if self.stage == "secondary" else 0})
            out.append(vals)
        return out

    def action_refresh(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration refreshes a report card from the gradebook."))
        self.check_access("write")
        self._refresh_lines()
        return True

    # --- Issuing --------------------------------------------------------------------------------

    def _snapshot_dict(self):
        """Everything printed on the card, in a form that does not depend on the database."""
        self.ensure_one()
        card = self.sudo()
        return {
            "format": "symbifox-report-card/1",
            "school": card.school_id.name, "year": card.year_id.name,
            "term": card.term_id.sequence, "kind": card.kind, "version": card.version, "lang": card.lang,
            "student": {"name": card.student_id.name,
                        "permanent_code": card.student_id.student_permanent_code or None},
            "group": card.group_id.name, "level": card.level_id.name or None,
            "success_threshold": card.year_id.success_threshold,
            "lines": [{
                "subject": l.subject_name, "teachers": l.teacher_names or None, "mode": l.mode,
                "result": l.percent if l.has_percent else None,
                "group_average": l.group_average if l.has_percent else None,
                "components": [{
                    "name": c.name, "result": c.percent if c.has_percent else None,
                    "final": c.final_percent if c.has_final else None} for c in l.component_ids],
                "final": l.final_percent if l.has_final else None,
                "exam": l.exam_percent if l.has_exam else None,
                "final_group_average": l.final_group_average if l.has_final else None,
                "outcome": l.outcome or None, "provisional": bool(l.provisional), "units": l.units or None,
                "units_earned": l.units_earned if l.has_final else None,
                "comment": l.comment or None} for l in card.line_ids.sorted("sequence")],
            "comment": card.comment or None,
            "decision": card.decision or None, "decision_note": card.decision_note or None,
        }

    @staticmethod
    def _seal(data):
        text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return text, hashlib.sha256(text.encode()).hexdigest()

    def _check_ready_to_issue(self):
        for card in self.sudo():
            if card.state != "draft":
                raise UserError(_("%s is not a draft.", card.display_name))
            if not card.line_ids:
                raise UserError(_("%s has no subject: generate it from the gradebook first.", card.display_name))
            stuck = card.line_ids.filtered(lambda l: l.component_ids and not l.weights_ok)
            if stuck:
                raise UserError(_(
                    "The component weights of %(subjects)s do not total 100 %%: enter the evaluation "
                    "framework's weights in the course before issuing the report card of %(student)s.",
                    subjects=", ".join(stuck.mapped("subject_name")), student=card.student_id.name))
            if card.kind == "final" and not card.decision:
                raise UserError(_(
                    "The last report card of the year records the year-end decision: choose it for %s.",
                    card.student_id.name))

    def _first_version(self):
        """The first version of this report card: a correction is judged on the deadline the first one met or missed."""
        self.ensure_one()
        card = self
        while card.supersedes_id:
            card = card.supersedes_id
        return card

    def action_issue(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration issues report cards."))
        # 🔴 The records are read as the user before anything runs as the superuser: the rule of the company is
        # the user's. The group alone is no door to the report cards of another company.
        self.check_access("write")
        # The lines are read from the gradebook one last time: a grade corrected since the draft was made is on the card.
        for card in self.sudo().filtered(lambda c: c.state == "draft"):
            card._refresh_lines()
        self._check_ready_to_issue()
        now = fields.Datetime.now()
        for card in self.sudo():
            if card.supersedes_id:
                # A correction made after the deadline of a report card that was on time is not late: the families
                # had their report card in time, and have it replaced.
                late = bool(card._first_version().issued_late)
            else:
                late = bool(card.deadline and fields.Date.context_today(card) > card.deadline)
            text, digest = self._seal(card._snapshot_dict())
            card.write({"state": "issued", "issued_on": now, "issued_by_id": self.env.user.id,
                        "issued_late": late, "snapshot": text, "snapshot_sha256": digest})
            if card.supersedes_id:
                card.supersedes_id.write({"state": "superseded", "replaced_by_id": card.id})
            card._file_in_record()
            if late and not card.supersedes_id:
                card.message_post(body=_(
                    "Issued after the deadline of %s.", fields.Date.to_string(card.deadline)))
        self.env.ref("bf_school_report_card.ir_cron_render_pdfs").sudo()._trigger()
        return True

    def _content_dict(self):
        """What can only change by the gradebook or the teachers: the lines, the comments and the decision."""
        data = self._snapshot_dict()
        return {key: data[key] for key in ("lines", "comment", "decision", "decision_note")}

    def action_verify(self):
        """Does the card still say what was sealed? Guards against a change made behind the application.

        The seal must match its own text, and the lines, comments and decision on the card must match what
        was sealed. The names are not compared: a name corrected since is a correction, not a tampering.
        """
        self.ensure_one()
        self.check_access("read")
        if self.state == "draft":
            raise UserError(_("Only an issued report card is sealed."))
        card = self.sudo()
        sealed = json.loads(card.snapshot)
        _text, digest = self._seal(sealed)
        ok = digest == card.snapshot_sha256 and card._content_dict() == {
            key: sealed[key] for key in ("lines", "comment", "decision", "decision_note")}
        if ok and card.pdf:
            ok = hashlib.sha256(base64.b64decode(card.pdf)).hexdigest() == card.pdf_sha256
        message = _("The report card still matches its seal.") if ok else _(
            "The report card no longer matches the seal made when it was issued.")
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Report card"), "message": message, "type": "success" if ok else "danger",
            "sticky": not ok}}

    def _report_data(self):
        """What the PDF prints: the sealed snapshot of an issued card, so a name corrected later cannot
        change a report card that was sent. A draft prints what it holds now."""
        self.ensure_one()
        if not self.env.su:
            # 🔴 Every subject and every comment: the office and the teachers of the group, like the PDF's download.
            self.check_access("read")
            if not (self.env.user.has_group(MANAGER) or self.env.user in self.group_id.sudo().teacher_ids):
                raise AccessError(_("A teacher of one subject reads their own lines of a report card, not the whole of it."))
        card = self.sudo()
        return json.loads(card.snapshot) if card.state != "draft" and card.snapshot else card._snapshot_dict()

    def action_open_correct(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Correct a report card"),
            "res_model": "bf.school.report.card.correct", "view_mode": "form", "target": "new",
            "context": {"default_card_id": self.id}}

    def action_correct(self):
        """A corrected version of an issued card: a new draft that will replace it."""
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration corrects a report card."))
        self.check_access("write")
        if self.state != "issued":
            raise UserError(_("Only an issued report card is corrected."))
        reason = self.env.context.get("correction_reason")
        if not reason:
            raise UserError(_("Say why the report card is corrected."))
        new = self.sudo().copy({
            "version": self.version + 1, "supersedes_id": self.id, "state": "draft", "correction_reason": reason,
            "comment": self.comment, "decision": self.decision, "decision_note": self.decision_note})
        new._refresh_lines()
        comments = {l.course_id.id: l.comment for l in self.sudo().line_ids if l.comment}
        for line in new.line_ids.filtered(lambda l: l.course_id.id in comments):
            line.comment = comments[line.course_id.id]
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": new.id, "view_mode": "form"}

    # --- The student's file ---------------------------------------------------------------------

    def _file_in_record(self):
        """File the issued card as the "school results" piece of the student's file."""
        Record = self.env["bf.school.record"].sudo()
        kind = self.env.ref("bf_school_record.type_results", raise_if_not_found=False)
        for card in self.sudo():
            record = Record.search([("student_id", "=", card.student_id.id), ("school_id", "=", card.school_id.id)],
                                   limit=1)
            if not (record and kind):
                continue
            existing = self.env["bf.school.record.document"].sudo().search([
                ("source_model", "=", self._name), ("source_res_id", "=", card.id)])
            if existing:
                continue
            self.env["bf.school.record.document"].sudo().create({
                "record_id": record.id, "type_id": kind.id, "state": "received",
                "note": _("Report card, term %(term)s, %(year)s", term=card.term_id.sequence, year=card.year_id.name),
                "source_model": self._name, "source_res_id": card.id})

    # --- Publication to the families ------------------------------------------------------------

    def action_publish(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration shows report cards to the families."))
        self.check_access("write")  # 🔴 as the user, before the superuser: the company's rule
        for card in self.sudo():
            if card.state != "issued":
                raise UserError(_("Issue the report card before showing it to the families."))
            if card.published_on:
                continue
            card.write({"published_on": fields.Datetime.now(), "published_late": card._published_late()})
            card._notify_families()
        return True

    def _published_late(self):
        """Shown after the deadline? A correction takes the answer of the version the families had before it."""
        self.ensure_one()
        previous = self.supersedes_id
        while previous:
            if previous.published_on:
                return bool(previous.published_late)
            previous = previous.supersedes_id
        return bool(self.deadline and fields.Date.context_today(self) > self.deadline)

    def _recipients(self):
        """Who the card is sent to: the holders of parental authority, or the student themself once of age (s. 29.1)."""
        self.ensure_one()
        if self.recipient == "student":
            return self.student_id.sudo()
        return self.student_id.sudo().student_guardian_link_ids.filtered("has_parental_authority").guardian_id

    def _notify_families(self):
        for card in self.sudo():
            # A corrected version says so: it replaces the report card the family already has.
            template = self.env.ref(
                "bf_school_report_card.mail_template_report_card_correction" if card.version > 1
                else "bf_school_report_card.mail_template_report_card", raise_if_not_found=False)
            if not template:
                continue
            told = self.env["res.partner"]
            for adult in card._recipients().filtered("email"):
                lang = adult.lang or card.lang
                queued = template.with_context(lang=lang, school_lang=lang).send_mail(
                    card.id, force_send=False, email_layout_xmlid=self.env["bf.school"]._school_mail_layout(),
                    email_values={"recipient_ids": [(6, 0, adult.ids)], "email_to": False})
                if queued:
                    told |= adult
            # 🔴 The email is deleted once it is sent: the thread keeps who was told, never what was said.
            card.message_post(body=_(
                "Shown to the families. Told by email: %s.", ", ".join(told.mapped("name")))
                if told else _("Shown to the families. No adult has an email address: the report card is in the portal."))
        self.env.ref("mail.ir_cron_mail_scheduler_action").sudo()._trigger()

    # --- The PDF --------------------------------------------------------------------------------

    def _render_pdf(self):
        """The PDF of an issued card, rendered from the sealed content.

        🔴 Never in a request a family waits on: wkhtmltopdf froze a family's page for more than a
        minute in the school suite's first lot. The cron renders them; the office has a button.
        One card that cannot be rendered says why on itself and does not stop the others.
        """
        report = self.env.ref("bf_school_report_card.action_report_card").sudo()
        for card in self.sudo():
            if card.state == "draft":
                raise UserError(_("The PDF of a draft is not kept: issue the report card first."))
            try:
                with self.env.cr.savepoint():
                    content, _kind = report.with_context(lang=card.lang)._render_qweb_pdf(report.report_name, card.ids)
                    before = card.pdf_sha256
                    card.write({"pdf": base64.b64encode(content), "pdf_error": False,
                                "pdf_sha256": hashlib.sha256(content).hexdigest(),
                                "pdf_name": "%s-t%s-v%s.pdf" % (card.student_id.name.replace(" ", "_"),
                                                                 card.term_id.sequence, card.version)})
                    if before:
                        # 🔴 The file that was handed out is replaced: its fingerprint is no longer on the card,
                        # so the thread and the file's access log keep it.
                        card.message_post(body=_(
                            "The PDF was rendered again. SHA-256 before: %(before)s. After: %(after)s.",
                            before=before, after=card.pdf_sha256))
                        card._log_in_record("piece_replaced", _("Report card, %(term)s: PDF rendered again",
                                                                 term=card._record_note()))
            except Exception as exc:  # noqa: BLE001  one report card must not stop the others
                _logger.warning("PDF of report card %s not rendered: %s", card.id, exc)
                card.write({"pdf_error": str(exc)[:200]})
        return True

    def action_render_pdf(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration renders the PDF."))
        self.check_access("write")  # 🔴 as the user, before the superuser: the company's rule
        self.sudo().write({"pdf_error": False})
        return self._render_pdf()

    def _pdf_matches_seal(self):
        """Is the PDF kept the file whose SHA-256 was written when it was rendered?"""
        self.ensure_one()
        pdf = self.sudo().pdf
        return bool(pdf and self.pdf_sha256 and hashlib.sha256(base64.b64decode(pdf)).hexdigest() == self.pdf_sha256)

    def _record_note(self):
        self.ensure_one()
        return _("term %(term)s, version %(version)s", term=self.term_id.sequence, version=self.version)

    def _log_in_record(self, action, note):
        """A line of the access log of the student's file, when the school keeps one for this student."""
        for card in self.sudo():
            record = self.env["bf.school.record"].sudo().search([
                ("student_id", "=", card.student_id.id), ("school_id", "=", card.school_id.id)], limit=1)
            if record:
                record.with_env(self.env)._log(action, note)

    def _school_log_download(self, field_name):
        """Called when the PDF leaves by the standard download routes (`/web/content`): the access log hears of it.

        Returns True when the PDF must not be given out: it no longer matches its SHA-256 (the line says so).
        """
        refused = False
        if field_name == "pdf":
            for card in self:
                if card._pdf_matches_seal():
                    card._log_in_record("piece_downloaded", _("Report card, %(term)s", term=card._record_note()))
                else:
                    card._log_in_record("seal_mismatch", _("Report card, %(term)s", term=card._record_note()))
                    refused = True
        return refused

    @api.model
    def _cron_render_pdfs(self, limit=10):
        waiting = [("state", "in", ("issued", "superseded")), ("pdf", "=", False), ("pdf_error", "=", False)]
        cards = self.sudo().search(waiting, limit=limit)
        cards._render_pdf()
        if self.sudo().search_count(waiting):
            self.env.ref("bf_school_report_card.ir_cron_render_pdfs").sudo()._trigger()

    def action_download(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": "/school/report-card/%s/pdf" % self.id, "target": "self"}


class SchoolReportCardLine(models.Model):
    """A subject on a report card: results copied from the gradebook, and the teacher's comment."""

    _name = "bf.school.report.card.line"
    _description = "Report card line"
    _order = "card_id, sequence, id"

    card_id = fields.Many2one("bf.school.report.card", required=True, ondelete="cascade", index=True)
    state = fields.Selection(related="card_id.state")
    company_id = fields.Many2one(related="card_id.company_id", store=True)
    student_id = fields.Many2one(related="card_id.student_id", store=True, index=True)
    term_id = fields.Many2one(related="card_id.term_id", store=True)
    sequence = fields.Integer(default=10)
    course_id = fields.Many2one("bf.school.course", ondelete="set null")
    subject_name = fields.Char("Subject")
    teacher_names = fields.Char("Teachers")
    mode = fields.Selection(
        [("discipline", "Discipline result only"), ("competency", "By competency"),
         ("volet", "By theory and practice component")])
    weights_ok = fields.Boolean(default=True)
    has_percent = fields.Boolean()
    percent = fields.Integer("Result (%)")
    group_average = fields.Integer("Group average (%)")
    has_final = fields.Boolean()
    final_percent = fields.Integer("Final result (%)")
    provisional = fields.Boolean(help="The course ends with a ministerial exam whose result is not in: the final result is the school's alone.")
    has_exam = fields.Boolean()
    exam_percent = fields.Integer("Ministerial exam (%)")
    final_group_average = fields.Integer("Final group average (%)")
    outcome = fields.Selection([("passed", "Passed"), ("failed", "Not passed")])
    units = fields.Integer()
    units_earned = fields.Integer("Units earned")
    comment = fields.Text("Comment")
    component_ids = fields.One2many("bf.school.report.card.component", "line_id", "Competencies and components")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("The subjects of a report card come from the gradebook."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            # 🔴 A teacher writes the comment, on the lines of their own courses. The figures are the
            # gradebook's: a correction is a corrected grade, then a refresh.
            if set(vals) - {"comment"}:
                raise AccessError(_("The figures of a report card come from the gradebook."))
            # The state is read for the card, whether or not the teacher of one subject may read the card itself.
            if any(state != "draft" for state in self.sudo().mapped("card_id.state")):
                raise UserError(_("An issued report card is frozen: a correction is a new version."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("The subjects of a report card come from the gradebook."))
        return super().unlink()


class SchoolReportCardComponent(models.Model):
    """One competency or component of a subject on the report card."""

    _name = "bf.school.report.card.component"
    _description = "Report card competency or component"
    _order = "line_id, sequence, id"

    line_id = fields.Many2one("bf.school.report.card.line", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char()
    has_percent = fields.Boolean()
    percent = fields.Integer("Result (%)")
    has_final = fields.Boolean()
    final_percent = fields.Integer("Final result (%)")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("The components of a report card come from the gradebook."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("The components of a report card come from the gradebook."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("The components of a report card come from the gradebook."))
        return super().unlink()


class SchoolReportCardRead(models.Model):
    """A parent opened an issued report card: the proof it reached them."""

    _name = "bf.school.report.card.read"
    _description = "Report card read receipt"
    _order = "read_on desc"

    card_id = fields.Many2one("bf.school.report.card", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="card_id.company_id", store=True)
    guardian_id = fields.Many2one("res.partner", "Read by", required=True, ondelete="restrict")
    read_on = fields.Datetime(default=fields.Datetime.now, required=True)

    _sql_constraints = [
        ("card_guardian_unique", "UNIQUE(card_id, guardian_id)", "One receipt per adult and report card."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("A read receipt is written by the portal."))
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_("A read receipt is not edited."))
