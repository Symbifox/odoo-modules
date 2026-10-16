import base64
import hashlib
import json
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..charlemagne import definitions, fic, gpg, reports, sftp, xml_spec
from .config import MANAGER

_logger = logging.getLogger(__name__)

#: Written by the buttons and the system only.
BATCH_SYSTEM_FIELDS = {"state", "mode", "xml", "xml_sha256", "sealed", "sealed_name", "sealed_sha256", "built_on",
                       "sent_on", "sent_by_id", "answered_on", "file_base", "command_number", "request_count"}

#: The order requests go in: a declaration before its results, registrations before results of exams.
KIND_ORDER = {"DJ": 1, "RJ": 2, "IJ": 3, "UJ": 4, "FI": 5}
#: The elements that make the functional key of a local result (guide 6.24): a modification cannot change them.
RESULT_KEY = ("CD_PERMN", "CD_ORGNS", "CD_COURS", "AN_OBTEN_RESLT", "SESN_OBTEN_RESLT", "NO_GROUP_ELEVE")
#: The school's own result and the rank go together (CHS-340010 and 340011).
SCHOOL_RESULT = ("NOTE_ECOLE_BRUT", "RANG_CINQM")
#: The August session of the exams: a result of that session carries no school result (CHS-340838) and so no rank (CHS-340010).
AUGUST = 8
OUTCOME_STATE = {"accepted": "accepted", "accepted_message": "accepted_message",
                 "rejected": "rejected", "suspended": "suspended"}


class CharlemagneLedger(models.Model):
    """What the Ministry has accepted from this school, by functional key.

    Charlemagne wants a creation once, then modifications; sending a creation twice is refused.
    The ledger is how the next transmission knows which it is: it holds the digest of what was
    accepted, written when an accepted answer comes back, never when a file is sent.

    🔴 One ledger per mode. A rehearsal in simulation accepts everything: if it wrote the same ledger
    the production would find every student "declared" and send nothing.
    """

    _name = "bf.school.charlemagne.ledger"
    _description = "What Charlemagne accepted"
    _order = "school_id, key"
    _rec_name = "key"

    school_id = fields.Many2one("bf.school", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    mode = fields.Selection([("simulation", "Simulation"), ("production", "Production")], required=True,
                            default="production", index=True)
    key = fields.Char(required=True, index=True)
    kind = fields.Selection([("DJ", "Declaration"), ("RJ", "Local result"), ("IJ", "Exam registration"),
                             ("UJ", "Exam result")], required=True)
    student_id = fields.Many2one("res.partner", "Student", ondelete="restrict", index=True)
    state = fields.Selection([("accepted", "Accepted"), ("withdrawn", "Cancelled")], default="accepted", required=True)
    digest = fields.Char(readonly=True)
    values = fields.Text(readonly=True)
    batch_id = fields.Many2one("bf.school.charlemagne.batch", ondelete="set null")
    updated_on = fields.Datetime(default=fields.Datetime.now)

    _sql_constraints = [("school_key_mode_unique", "UNIQUE(school_id, key, mode)", "One ledger line per functional key.")]

    @api.model
    def _digest(self, values):
        return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("The ledger is written by the answers of the Ministry."))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("The ledger is written by the answers of the Ministry."))
        return super().create(vals_list)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("The ledger is written by the answers of the Ministry."))
        return super().unlink()


class CharlemagneMessage(models.Model):
    """A message about a transaction or a whole transmission: ours before sending, the Ministry's after."""

    _name = "bf.school.charlemagne.message"
    _description = "Charlemagne message"
    _order = "id"

    batch_id = fields.Many2one("bf.school.charlemagne.batch", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True)
    transaction_id = fields.Many2one("bf.school.charlemagne.transaction", ondelete="cascade", index=True)
    source = fields.Selection([("local", "Checked here"), ("ministry", "The Ministry")], required=True)
    number = fields.Char()
    consequence = fields.Selection([("A", "Accepted with a message"), ("R", "Rejected"), ("S", "Held back")])
    text = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Messages are written by the checks and the answers of the Ministry."))
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_("A message is not edited."))


class CharlemagneAnswer(models.Model):
    """A file the Ministry left for a transmission, read once.

    Its name is how the next fetch knows it was read and saved: only then is it taken off the server.
    """

    _name = "bf.school.charlemagne.answer"
    _description = "File returned by Charlemagne"
    _order = "id"

    batch_id = fields.Many2one("bf.school.charlemagne.batch", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True)
    name = fields.Char(required=True)
    kind = fields.Selection([("command", "Report of the command"), ("request", "Report of a request"),
                             ("file", "Other file"), ("error", "Not read")], required=True)
    sha256 = fields.Char("SHA-256")
    received_on = fields.Datetime("Received on", default=fields.Datetime.now)
    error = fields.Char()

    _sql_constraints = [("batch_name_unique", "UNIQUE(batch_id, name)", "A returned file is read once.")]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Returned files are recorded by the fetch of the answers."))
        return super().create(vals_list)

    def write(self, vals):
        raise UserError(_("A returned file is not edited."))


class CharlemagneTransaction(models.Model):
    """One line of a transmission: create, modify or cancel one thing for one student."""

    _name = "bf.school.charlemagne.transaction"
    _description = "Charlemagne transaction"
    _order = "batch_id, sequence, id"

    batch_id = fields.Many2one("bf.school.charlemagne.batch", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="batch_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    kind = fields.Selection(
        [("DJ", "Declaration (FGJ)"), ("RJ", "Local result (FGJ)"), ("IJ", "Exam registration"),
         ("UJ", "Exam result"), ("FI", "Request for a file")], required=True)
    code = fields.Char(required=True, help="CRE, MOD, SUP, or the code of a request for a file.")
    student_id = fields.Many2one("res.partner", "Student", ondelete="restrict", index=True)
    key = fields.Char(index=True)
    ident = fields.Char("Transaction id", help="What the Ministry reports back, so each answer finds its line.")
    values = fields.Text(help="The elements of the transaction, as JSON.")
    digest = fields.Char()
    ledger_values = fields.Text()
    merge = fields.Boolean("Adds to what was accepted", help="The ledger keeps the union of what was accepted (exam results).")
    state = fields.Selection(
        [("draft", "To check"), ("invalid", "Does not pass the checks"), ("ready", "Ready"),
         ("accepted", "Accepted"), ("accepted_message", "Accepted with a message"),
         ("rejected", "Rejected"), ("suspended", "Held back")], default="draft", required=True)
    message_ids = fields.One2many("bf.school.charlemagne.message", "transaction_id", "Messages")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("Transactions are prepared by the collection of the transmission."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(_("Transactions are prepared by the collection of the transmission."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(tx.batch_id.state != "draft" for tx in self):
            raise UserError(_("A transmission that was built is not edited: start a new one."))
        return super().unlink()

    def _values(self):
        self.ensure_one()
        return json.loads(self.values or "{}")


class CharlemagneBatch(models.Model):
    """A transmission: what the school sends the Ministry in one file, and what comes back."""

    _name = "bf.school.charlemagne.batch"
    _description = "Charlemagne transmission"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(required=True, copy=False, default=lambda s: _("New"), readonly=True)
    config_id = fields.Many2one("bf.school.charlemagne.config", "Settings", required=True, ondelete="restrict", index=True)
    school_id = fields.Many2one(related="config_id.school_id", store=True, index=True)
    company_id = fields.Many2one(related="config_id.company_id", store=True)
    mode = fields.Selection(
        [("simulation", "Simulation"), ("production", "Production")], readonly=True, copy=False,
        help="The mode of the settings when this transmission was made. A transmission and the ledger it writes "
             "belong to one mode for good.")
    year_id = fields.Many2one("bf.school.year", "School year", required=True, ondelete="restrict",
                              domain="[('school_id', '=', school_id)]")
    state = fields.Selection(
        [("draft", "Being prepared"), ("built", "File built"), ("sealed", "Signed and encrypted"),
         ("sent", "Sent"), ("answered", "Answered"), ("refused", "Refused by the Ministry")],
        default="draft", required=True, tracking=True, copy=False)
    include_declarations = fields.Boolean("Declarations", default=True)
    include_results = fields.Boolean("Local results", default=True)
    include_exams = fields.Boolean("Exam registrations and results", default=True)
    transaction_ids = fields.One2many("bf.school.charlemagne.transaction", "batch_id", "Transactions")
    message_ids = fields.One2many("bf.school.charlemagne.message", "batch_id", "Messages",
                                  domain=[("transaction_id", "=", False)])
    answer_ids = fields.One2many("bf.school.charlemagne.answer", "batch_id", "Files returned")
    collect_log = fields.Text("What was left out", readonly=True, copy=False)
    file_base = fields.Char("File name", copy=False, help="The name of the file without its extension (the guide's ZzZ).")
    command_number = fields.Char("Command number", copy=False, help="Given by the Ministry in its answer.")
    request_count = fields.Integer("Requests in the file", copy=False,
                                   help="The Ministry returns one report per request: the transmission is answered "
                                        "when each has come back.")
    xml = fields.Binary("Unencrypted XML file", attachment=True, copy=False, groups=MANAGER)
    xml_sha256 = fields.Char("XML SHA-256", copy=False)
    sealed = fields.Binary("Signed and encrypted file", attachment=True, copy=False, groups=MANAGER)
    sealed_name = fields.Char("Signed file name", copy=False)
    sealed_sha256 = fields.Char("File SHA-256", copy=False)
    built_on = fields.Datetime(copy=False)
    sent_on = fields.Datetime(copy=False, tracking=True)
    sent_by_id = fields.Many2one("res.users", copy=False)
    answered_on = fields.Datetime(copy=False)
    transaction_count = fields.Integer(compute="_compute_counts")
    invalid_count = fields.Integer(compute="_compute_counts")
    rejected_count = fields.Integer(compute="_compute_counts")
    accepted_count = fields.Integer(compute="_compute_counts")
    error_count = fields.Integer("Files not read", compute="_compute_counts")

    @api.depends("transaction_ids.state", "answer_ids.kind")
    def _compute_counts(self):
        for batch in self:
            batch.error_count = len(batch.answer_ids.filtered(lambda a: a.kind == "error"))
            states = batch.transaction_ids.mapped("state")
            batch.transaction_count = len(states)
            batch.invalid_count = states.count("invalid")
            batch.rejected_count = states.count("rejected") + states.count("suspended")
            batch.accepted_count = states.count("accepted") + states.count("accepted_message")

    # --- Guards ---------------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        """With a single set of settings and a current school year, a new transmission starts from them."""
        values = super().default_get(fields_list)
        if not values.get("config_id"):
            configs = self.env["bf.school.charlemagne.config"].search([], limit=2)
            if len(configs) == 1:
                values["config_id"] = configs.id
        if values.get("config_id") and not values.get("year_id"):
            school = self.env["bf.school.charlemagne.config"].browse(values["config_id"]).school_id
            year = self.env["bf.school.year"].search([("school_id", "=", school.id), ("state", "=", "current")], limit=1)
            if year:
                values["year_id"] = year.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        self._check_office()
        for vals in vals_list:
            # 🔴 The web client sends the default of every field on the screen (a counter at 0): an empty value forges nothing.
            forbidden = {f for f in set(vals) & (BATCH_SYSTEM_FIELDS - {"state", "name"}) if vals[f]}
            if not self.env.su and (forbidden or vals.get("state", "draft") != "draft"):
                raise AccessError(_("A transmission's state and files move with the buttons."))
            if vals.get("config_id"):
                vals["mode"] = self.env["bf.school.charlemagne.config"].browse(vals["config_id"]).sudo().mode
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("bf.school.charlemagne.batch") or _("New")
        return super().create(vals_list)

    def write(self, vals):
        self._check_office()
        if not self.env.su and set(vals) & BATCH_SYSTEM_FIELDS:
            # 🔴 `readonly` is no guard against RPC: the state, the files and the seal are the buttons'.
            raise AccessError(_("A transmission's state and files move with the buttons."))
        if not self.env.su and any(batch.state != "draft" for batch in self) and (
                {"year_id", "config_id", "include_declarations", "include_results", "include_exams"} & set(vals)):
            raise UserError(_("A transmission that was built is not edited: start a new one."))
        return super().write(vals)

    def unlink(self):
        if any(batch.state not in ("draft", "built") and batch.mode == "production" for batch in self):
            raise UserError(_("A transmission that was sent is kept: it is the school's record of what the Ministry received."))
        return super().unlink()

    @api.model
    def _check_office(self):
        if not self.env.su and not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration prepares transmissions."))

    def _check_mode(self):
        for batch in self:
            if batch.config_id.mode != batch.mode:
                raise UserError(_(
                    "The settings changed from %(was)s to %(now)s since this transmission was made: start a new one, "
                    "so what was accepted in one mode is not taken for what was accepted in the other.",
                    was=batch.mode, now=batch.config_id.mode))

    # --- Collecting -------------------------------------------------------------------------------

    def _tx(self, kind, code, key, values, student=None, ledger_values=None, merge=False):
        """Create a transaction line (sudo: the collection is the system's)."""
        self.ensure_one()
        sequence = KIND_ORDER[kind] * 1000 + len(self.transaction_ids) + 1
        permanent = (values.get("CD_PERMN") if isinstance(values, dict) else None) or "NA"
        # The guide asks for a part of the functional key: the student's code, what it is, and the ids that follow.
        tail = (key or "").split("|")[2:]
        ident = "-".join([permanent, kind + code] + tail)[:80]
        return self.env["bf.school.charlemagne.transaction"].sudo().create({
            "batch_id": self.id, "kind": kind, "code": code, "student_id": student.id if student else False,
            "key": key, "ident": ident, "sequence": sequence, "values": json.dumps(values, ensure_ascii=False),
            "digest": self.env["bf.school.charlemagne.ledger"]._digest(values),
            "ledger_values": json.dumps(ledger_values if ledger_values is not None else values, ensure_ascii=False),
            "merge": merge})

    def _problem(self, kind, student, key, reason):
        """A line the collection could not make: shown as invalid, with the reason, so nobody is left out silently."""
        tx = self._tx(kind, "CRE", key, {}, student)
        tx.state = "invalid"
        self.env["bf.school.charlemagne.message"].sudo().create({
            "batch_id": self.id, "transaction_id": tx.id, "source": "local", "number": "LOCAL-010",
            "consequence": "R", "text": reason})
        return tx

    def _ledger(self, key):
        return self.env["bf.school.charlemagne.ledger"].sudo().search(
            [("school_id", "=", self.school_id.id), ("key", "=", key), ("mode", "=", self.mode)], limit=1)

    def action_collect(self):
        """Make the transactions: what is new or changed since the Ministry last accepted it."""
        self.ensure_one()
        self._check_office()
        if self.state != "draft":
            raise UserError(_("Only a transmission being prepared can collect transactions."))
        self._check_mode()
        self.config_id._check_ready_for("collect")
        self.transaction_ids.sudo().unlink()
        self.message_ids.sudo().unlink()
        self.sudo().collect_log = False
        notes = []
        waiting = self.sudo().search([("id", "!=", self.id), ("school_id", "=", self.school_id.id),
                                      ("mode", "=", self.mode), ("state", "=", "sent")])
        if waiting:
            # The ledger is written by the answers: until they are in, what these hold is collected again.
            notes.append(_("Not answered yet: %s. What these hold is collected again here, and the Ministry refuses a "
                           "creation it already has. Wait for the answers, or expect those refusals.",
                           ", ".join(waiting.mapped("name"))))
        if self.include_declarations:
            notes += self._collect_declarations()
        if self.include_results:
            notes += self._collect_results()
        if self.include_exams:
            notes += self._collect_exams()
        self.sudo().collect_log = "\n".join(notes) or False
        return True

    def _collect_declarations(self):
        Profile = self.env["bf.school.charlemagne.profile"].sudo()
        Profile._ensure_profiles(self.year_id)
        notes = []
        for profile in Profile.search([("year_id", "=", self.year_id.id)]):
            key = profile._dj_key()
            entry = self._ledger(key)
            student = profile.student_id
            if profile.withdraw:
                if entry and entry.state == "accepted":
                    old = json.loads(entry.values)
                    sup = {k: old[k] for k in ("CD_PERMN", "NOM", "PRENM", "AN_SCOLR_PPS", "CD_ORGNS", "TYPE_DECLR",
                                                "DT_DEBUT_FREQN") if k in old}
                    self._tx("DJ", "SUP", key, sup, student, ledger_values=old)
                continue
            try:
                values = profile._dj_values()
            except ValidationError as exc:
                self._problem("DJ", student, key, exc.args[0])
                continue
            if not entry:
                self._tx("DJ", "CRE", key, values, student)
            elif entry.state == "withdrawn":
                notes.append(_("%s: the declaration was cancelled; it is not made again here.", student.name))
            elif self.env["bf.school.charlemagne.ledger"]._digest(values) != entry.digest:
                old = json.loads(entry.values)
                changed = dict(values)
                if old.get("DT_DEBUT_FREQN") and old.get("DT_DEBUT_FREQN") != values.get("DT_DEBUT_FREQN"):
                    changed["ANCIE_DT_DEBUT_FREQN"] = old["DT_DEBUT_FREQN"]
                self._tx("DJ", "MOD", key, changed, student, ledger_values=values)
        return notes

    def _collect_results(self):
        """The school's final result of each course without a ministerial exam, once the third term is closed.

        A course with a ministerial exam is not sent here: the Ministry refuses a local result for it (CHS-340701)
        and takes the school's result with the exam result (the "note école" of the exam transaction).
        """
        notes = []
        year = self.year_id
        third = year.term_ids.filtered(lambda t: t.sequence == 3)
        if not third or third.state != "closed":
            return [_("The third term is not closed: no local result is collected before it is.")]
        Result = self.env["bf.school.course.result"].sudo()
        for course in self.env["bf.school.course"].sudo().search([("year_id", "=", year.id)]):
            if not course.ministry_course_code or course.ministerial_exam:
                continue
            if not course.group_id.ministry_group_number:
                notes.append(_("%s: the group has no Ministry number, its results are not collected.", course.display_name))
                continue
            if not course.weights_ok:
                # The report card refuses the same course: its result would be worked out on weights nobody confirmed.
                notes.append(_("%s: the component weights do not total 100 %%, its results are not collected.",
                               course.display_name))
                continue
            finals = course._compute_finals()
            ranks = course._fifth_ranks()
            for student_id in course._students().ids:
                data = finals.get(student_id)
                student = self.env["res.partner"].sudo().browse(student_id)
                key = "RJ|%s|%s|%s" % (student_id, course.id, year.id)
                if not data or data["school_final"] is None:
                    continue
                if not Result.search_count([("course_id", "=", course.id), ("term_id", "=", third.id),
                                            ("student_id", "=", student_id), ("has_percent", "=", True)]):
                    notes.append(_("%(student)s has no result for the third term in %(course)s yet.",
                                   student=student.name, course=course.display_name))
                    continue
                if not student.student_permanent_code:
                    self._problem("RJ", student, key, _("%s has no permanent code.", student.name))
                    continue
                values = {
                    "CD_PERMN": student.student_permanent_code, "CD_ORGNS": self.school_id.code,
                    "CD_COURS": course.ministry_course_code, "AN_OBTEN_RESLT": str(year.date_end.year),
                    "SESN_OBTEN_RESLT": "%02d" % self.config_id.local_result_month,
                    "NO_GROUP_ELEVE": "%02d" % course.group_id.ministry_group_number,
                    "NOTE_ECOLE_BRUT": str(data["school_final"])}
                if ranks.get(student_id):
                    values["RANG_CINQM"] = str(ranks[student_id])  # required with every result of the FGJ (CHS-340011)
                entry = self._ledger(key)
                if entry:
                    # The key of a result is not modifiable (guide 6.24): the Ministry holds it as it was accepted, so the
                    # modification names that record; the school is told that its own figures have moved.
                    held = json.loads(entry.values or "{}")
                    moved = {tag: held[tag] for tag in RESULT_KEY if tag in held and held[tag] != values.get(tag)}
                    if moved:
                        values = dict(values, **moved)
                        notes.append(_("%(student)s, %(course)s: the Ministry holds this result under %(held)s, which "
                                       "cannot be modified: the new result is sent for that record.", student=student.name,
                                       course=course.display_name,
                                       held=", ".join("%s %s" % (tag, moved[tag]) for tag in moved)))
                if not entry:
                    self._tx("RJ", "CRE", key, values, student)
                elif self.env["bf.school.charlemagne.ledger"]._digest(values) != entry.digest:
                    self._tx("RJ", "MOD", key, values, student)
        return notes

    @staticmethod
    def _exam_suffix(student_id, course_id, year, month):
        return "%s|%s|%s|%s" % (student_id, course_id, year, month)

    def _collect_exams(self):
        notes = []
        courses = self.env["bf.school.course"].sudo().search(
            [("year_id", "=", self.year_id.id), ("ministerial_exam", "=", True)])
        # What the school still holds, by the ids alone: a registration whose course lacks a code today is still held.
        held = {self._exam_suffix(exam.student_id.id, course.id, exam.exam_year, exam.exam_month)
                for course in courses for exam in course.exam_result_ids if exam.exam_year and exam.exam_month}
        notes += self._withdraw_exams(held)
        for course in courses:
            if not course.ministry_course_code or not course.group_id.ministry_group_number:
                notes.append(_("%s: the course code or the group number is missing, its exams are not collected.",
                               course.display_name))
                continue
            finals = course._compute_finals()
            ranks = course._fifth_ranks()
            unconfirmed = not course.weights_ok
            if unconfirmed:
                notes.append(_("%s: the component weights do not total 100 %%, the school's result is not sent with its "
                               "exams.", course.display_name))
            for exam in course.exam_result_ids:
                student = exam.student_id.sudo()
                if not exam.exam_year or not exam.exam_month:
                    notes.append(_("%(student)s: %(course)s has no exam sitting entered.", student=student.name,
                                   course=course.display_name))
                    continue
                base = {
                    "CD_PERMN": student.student_permanent_code, "CD_ORGNS": self.school_id.code,
                    "CD_COURS": course.ministry_course_code, "AN_EPREU_DNR": str(exam.exam_year),
                    "SESN_EPREU_DNR": "%02d" % exam.exam_month,
                    "NO_GROUP_ELEVE": "%02d" % course.group_id.ministry_group_number}
                suffix = self._exam_suffix(student.id, course.id, exam.exam_year, exam.exam_month)
                if not student.student_permanent_code:
                    self._problem("IJ", student, "IJ|" + suffix, _("%s has no permanent code.", student.name))
                    continue
                registered = self._ledger("IJ|" + suffix)
                if not registered or registered.state == "withdrawn":
                    self._tx("IJ", "CRE", "IJ|" + suffix, dict(base), student)
                school_final = None if unconfirmed else (finals.get(student.id) or {}).get("school_final")
                elements = self._exam_elements(exam, school_final, None if unconfirmed else ranks.get(student.id))
                key = "UJ|" + suffix
                entry = self._ledger(key)
                old = json.loads(entry.values) if entry else {}
                if not elements and not old:
                    continue
                new = {k: v for k, v in elements.items() if k not in old}
                changed = {k: v for k, v in elements.items() if k in old and old[k] != v}
                # The school's result and the rank go together (CHS-340010 and 340011): a transaction that carries
                # one carries the other.
                for part in (new, changed):
                    if part and set(SCHOOL_RESULT) & set(part):
                        for tag in SCHOOL_RESULT:
                            if tag in elements:
                                part[tag] = elements[tag]
                # An element the school no longer has is taken off with an empty tag (guide 6.29). Not what is only
                # unconfirmed (the weights), and not the rank alone: a student who left the group has none any more.
                gone = [tag for tag in old if tag not in elements and not (unconfirmed and tag in SCHOOL_RESULT)]
                if "NOTE_ECOLE_BRUT" in elements:
                    gone = [tag for tag in gone if tag != "RANG_CINQM"]
                if new:
                    self._tx("UJ", "CRE", key, dict(base, **new), student, ledger_values=new, merge=True)
                if changed or gone:
                    self._tx("UJ", "MOD", key, dict(base, **changed, **{tag: "" for tag in gone}), student,
                             ledger_values=dict(changed, **{tag: None for tag in gone}), merge=True)
        return notes

    def _withdraw_exams(self, held):
        """What the Ministry accepted for an exam the school no longer holds: a sitting moved, a registration removed.

        The results go first, each element off with an empty tag (guide 6.29); the registration is cancelled once none is
        left, in the next transmission, because the Ministry does not cancel a registration while results were received
        (CHA-600814).
        """
        notes = []
        courses = set(self.env["bf.school.course"].sudo().search([("year_id", "=", self.year_id.id)]).ids)
        entries = self.env["bf.school.charlemagne.ledger"].sudo().search([
            ("school_id", "=", self.school_id.id), ("mode", "=", self.mode), ("kind", "in", ("IJ", "UJ")),
            ("state", "=", "accepted")])
        gone = {}
        for entry in entries:
            parts = entry.key.split("|")
            if len(parts) != 5 or not parts[2].isdigit() or int(parts[2]) not in courses:
                continue
            suffix = "|".join(parts[1:])
            if suffix not in held:
                gone.setdefault(suffix, {})[entry.kind] = entry
        for suffix, found in sorted(gone.items()):
            registration, result = found.get("IJ"), found.get("UJ")
            student = (registration or result).student_id.sudo()
            elements = json.loads(result.values) if result else {}
            if elements and registration:
                base = json.loads(registration.values)
                self._tx("UJ", "MOD", result.key, dict(base, **{tag: "" for tag in elements}), student,
                         ledger_values={tag: None for tag in elements}, merge=True)
                notes.append(_("%(student)s: the exam is no longer held, its results are taken off now and the "
                               "registration is cancelled in the next transmission.", student=student.name))
            elif registration:
                self._tx("IJ", "SUP", registration.key, json.loads(registration.values), student)
        return notes

    @staticmethod
    def _exam_elements(exam, school_final, rank):
        """What is known of an exam result, as the elements the Ministry takes.

        NOTE_ECOLE_BRUT is the school's own final result for the course (the "note école"): the Ministry combines it
        with the exam. A zero in a part's score is read as not entered (the Ministry refuses 0 % sent directly).
        The August session carries no school result, and so no rank: the Ministry refuses one (CHS-340838, CHS-340010).
        """
        elements = {}
        if school_final is not None and exam.exam_month != AUGUST:
            elements["NOTE_ECOLE_BRUT"] = str(school_final)
            if rank:
                elements["RANG_CINQM"] = str(rank)
        if exam.state == "absent":
            elements["NOTE_MINST_BRUT"] = "ABS"
        if exam.state in ("taken", "graded"):
            for field, tag in (("objective_score", "NOTE_CALC_PART_OBJEC"), ("development_score", "NOTE_CALC_PART_DEVLP"),
                               ("short_development_score", "NOTE_CALC_PART_DEVLP_COURT")):
                if exam[field]:
                    elements[tag] = str(exam[field])
        return elements

    def action_open_request(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Ask for a file"),
                "res_model": "bf.school.charlemagne.request", "view_mode": "form", "target": "new",
                "context": {"default_batch_id": self.id}}

    def action_add_file_request(self, code, parameters):
        """A request for a file (list, report, feedback) among the transactions."""
        self.ensure_one()
        self._check_office()
        if self.state != "draft":
            raise UserError(_("Only a transmission being prepared takes requests."))
        if code not in fic.FIC:
            raise UserError(_("Unknown request %s.", code))
        values = dict(parameters)
        tags = {c.tag for c in fic.FIC[code][1].children}
        if "CD_ORGNS" in tags:
            values.setdefault("CD_ORGNS", self.school_id.code)
        if "AN_SCOLR_PPS" in tags:
            values.setdefault("AN_SCOLR_PPS", "%s%s" % (self.year_id.date_start.year, self.year_id.date_end.year))
        return self._tx("FI", code, "FI|%s|%s" % (code, len(self.transaction_ids)), values)

    # --- Building ---------------------------------------------------------------------------------

    def _spec_for(self, tx):
        if tx.kind == "FI":
            return fic.FIC[tx.code][1]
        return definitions.TRANSACTIONS[(tx.kind, tx.code)]

    @staticmethod
    def _local_message(key, params):
        """The text of one of the local checks, in the reader's language."""
        texts = {
            "missing": _("The element %(tag)s is required."),
            "missing_structure": _("The structure %(tag)s is required."),
            "unknown": _("The element %(tag)s is not defined for this transaction."),
            "latin1": _("The value %(value)s for %(tag)s holds a character ISO-8859-1 cannot carry."),
            "once": _("The structure %(tag)s appears once at most."),
            "format": _("The value %(value)s for the element %(tag)s does not respect the expected format: %(format)s."),
        }
        if key == "format":
            kinds = {"alphanumeric": _("alphanumeric"), "numeric": _("numeric"), "date yyyy-mm-dd": _("date yyyy-mm-dd")}
            name, _sep, length = params["format"].partition(" (")
            params = dict(params, format=kinds.get(name, name) + (" (%s" % length if length else ""))
        return texts[key] % params

    def action_build(self):
        """Check each transaction against the guide and write the XML file when all pass."""
        self.ensure_one()
        self._check_office()
        if self.state not in ("draft", "built"):
            raise UserError(_("This transmission was already sealed."))
        self._check_mode()
        config = self.config_id
        config._check_ready_for("build")
        Message = self.env["bf.school.charlemagne.message"].sudo()
        transactions = self.transaction_ids.sudo().filtered(lambda t: t.state != "rejected")
        if not transactions:
            raise UserError(_("There is nothing to send: collect the transactions first."))
        Message.search([("batch_id", "=", self.id), ("source", "=", "local"), ("number", "!=", "LOCAL-010")]).unlink()
        elements = {}
        for tx in transactions:
            if tx.state == "invalid" and tx.message_ids.filtered(lambda m: m.number == "LOCAL-010"):
                continue
            root, errors = xml_spec.build_transaction(self._spec_for(tx), tx._values(), tx.kind, tx.code)
            for number, _tag, _text, key, params in errors:
                Message.create({"batch_id": self.id, "transaction_id": tx.id, "source": "local", "number": number,
                                "consequence": "R", "text": self._local_message(key, params)})
            tx.state = "invalid" if errors else "ready"
            if not errors:
                elements[tx.id] = root
        if self.transaction_ids.sudo().filtered(lambda t: t.state == "invalid"):
            # Not an exception: it would roll back the messages that say what to fix.
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "title": _("Transmission"), "type": "warning", "sticky": True,
                "message": _("%s transaction(s) do not pass the checks: fix what the messages say in the students' "
                             "Charlemagne cards, then collect and build again. Nothing is sent partially.", self.invalid_count)}}
        demands = []
        for (kind, organism), group in self._grouped(transactions):
            demands.append({
                "service": "FIC" if kind == "FI" else "TRA", "organism": organism, "type": kind,
                "lot": {"nolot": self.id, "responsible": config.responsible_name or self.env.user.name},
                "transactions": [{"code": tx.code, "id": tx.ident, "root": elements[tx.id],
                                  "organism": organism if kind != "FI" or "CD_ORGNS" in tx._values() else None}
                                 for tx in group]})
        transmitter = config.transmitter_code or config.organism_code
        blob = xml_spec.build_command(transmitter, "X", demands)
        base = self._file_base()
        self.sudo().write({
            "xml": base64.b64encode(blob), "xml_sha256": hashlib.sha256(blob).hexdigest(), "file_base": base,
            "request_count": len(demands), "built_on": fields.Datetime.now(), "state": "built", "sealed": False,
            "sealed_name": False, "sealed_sha256": False})
        return True

    def _grouped(self, transactions):
        """The transactions by request: one per kind and organism, declarations first."""
        groups = {}
        for tx in transactions.sorted("sequence"):
            organism = (tx._values().get("CD_ORGNS") if tx.kind == "FI" else None) or self.school_id.code
            groups.setdefault((tx.kind, organism), self.env["bf.school.charlemagne.transaction"])
            groups[(tx.kind, organism)] |= tx
        return sorted(groups.items(), key=lambda item: KIND_ORDER[item[0][0]])

    def _file_base(self):
        """The ZzZ of the file name: ASCII, at most 146 characters, unique per transmission."""
        code = re.sub(r"[^A-Za-z0-9]", "", self.school_id.code or "")
        # Ends with a letter: the Ministry may add a digit to a returned file's name, and "…_1" must not be found in "…_15".
        return "SYMBIFOX_%s_%s_%se" % (code, fields.Date.context_today(self).strftime("%Y%m%d"), self.id)

    # --- Sealing and sending ----------------------------------------------------------------------

    def action_seal(self):
        """Encrypt for the Ministry and sign as the school (production). In simulation only its identity is kept."""
        self.ensure_one()
        self._check_office()
        if self.state != "built":
            raise UserError(_("Build the file first."))
        self._check_mode()
        config = self.config_id
        config._check_ready_for("seal")
        blob = base64.b64decode(self.sudo().xml)
        if hashlib.sha256(blob).hexdigest() != self.xml_sha256:
            raise UserError(_("The XML file no longer matches the one that was built."))
        values = {"sealed_name": "%s.CHA" % self.file_base, "state": "sealed"}
        if config.mode == "production":
            try:
                ring, school_fpr, ministry_fpr = config._keyring()
                try:
                    sealed = ring.sign_and_encrypt(blob, ministry_fpr, school_fpr)
                finally:
                    ring.__exit__()
            except gpg.GpgError as exc:
                raise UserError(_("The file could not be signed and encrypted: %s", exc)) from exc
            values.update({"sealed": base64.b64encode(sealed), "sealed_sha256": hashlib.sha256(sealed).hexdigest()})
        else:
            # 🔴 A rehearsal keeps no copy of the FILE, neither the plain one nor an encrypted one: only its own identity.
            # The transactions and the ledger keep the elements, the students' identities among them: that is how the
            # rehearsal can go on (a creation, then a modification). Deleting the rehearsal takes its transactions away;
            # the ledger lines of the rehearsal stay.
            values["sealed_sha256"] = hashlib.sha256(blob).hexdigest()
        if not config.keep_plain_xml:
            values["xml"] = False
        self.sudo().write(values)
        return True

    def action_send(self):
        self.ensure_one()
        self._check_office()
        if self.state != "sealed":
            raise UserError(_("Sign and encrypt the file before sending it."))
        self._check_mode()
        config = self.config_id
        config._check_ready_for("send")
        if config.mode == "production":
            blob = base64.b64decode(self.sudo().sealed)
            with tempfile.TemporaryDirectory(prefix="bfc") as tmp:
                path = os.path.join(tmp, "file")
                with open(path, "wb") as handle:
                    handle.write(blob)
                try:
                    with config._sftp() as client:
                        client.put(path, "%s/%s" % (config.sudo().sftp_in_dir.strip("/"), self.sealed_name))
                except sftp.SftpError as exc:
                    raise UserError(_("The file could not be sent: %s", exc)) from exc
        self.sudo().write({"state": "sent", "sent_on": fields.Datetime.now(), "sent_by_id": self.env.user.id})
        self.message_post(body=_("Sent: %(name)s, SHA-256 %(sha)s.", name=self.sealed_name, sha=self.sealed_sha256))
        if config.mode == "simulation":
            self._simulate_answer()
        return True

    # --- The answers ------------------------------------------------------------------------------

    def _simulate_answer(self):
        """A stand-in for the Ministry: accepts every transaction that reached it, in the Ministry's report format.

        One report for the command and one for each request, as the guide says (section 3.4.1).
        """
        self.ensure_one()
        number = "%07d" % self.id
        requests = {}
        for tx in self.transaction_ids.sudo().filtered(lambda t: t.state == "ready"):
            requests.setdefault(tx.kind, []).append(tx)
        command = ET.Element("ECH01020299")
        node = ET.SubElement(command, "commande", {"noCommande": number, "nomFichier": self.sealed_name or "",
                                                   "formatTransmission": "XML"})
        for tag, text in (("codeTransactionCommande", "COM"), ("sigleSysteme", "Charlemagne"),
                          ("codeOrganismeTransmetteur", self.config_id.transmitter_code or self.config_id.organism_code or ""),
                          ("formatDiffusion", "XML"), ("nbDemandesTransmises", str(len(requests))),
                          ("nbDemandesAcceptees", str(len(requests))), ("nbDemandesRefusees", "0"),
                          ("nbDemandesSuspends", "0")):
            ET.SubElement(node, tag).text = text
        self._ingest("CHACO%s%s.XML" % (number, self.file_base), ET.tostring(command))
        for index, (kind, items) in enumerate(sorted(requests.items(), key=lambda i: KIND_ORDER[i[0]]), start=1):
            demand = ET.Element("ECH01020298", {"noCommande": number, "nomFichier": self.sealed_name or ""})
            request = ET.SubElement(demand, "Demande")
            ET.SubElement(request, "noDemande").text = str(index)
            ET.SubElement(request, "typeTransaction").text = kind
            counter = ET.SubElement(ET.SubElement(request, "listeCompteurTransaction"), "compteurTransaction")
            ET.SubElement(counter, "nbTransactionsTransmises").text = str(len(items))
            ET.SubElement(counter, "nbTransactionsAccepteesSansMessage").text = str(len(items))
            listing = ET.SubElement(demand, "listeTransaction")
            for tx in items:
                item = ET.SubElement(listing, "transactionSysteme")
                ET.SubElement(item, "codeTransactionSysteme").text = tx.code
                ET.SubElement(item, "identificationTransaction").text = tx.ident
            self._ingest("CHADE%07d%s.XML" % (index, self.file_base), ET.tostring(demand))

    def _answer_kind(self, name):
        """What a file of the return directory is for this transmission, or None when it is another's.

        The ZzZ of the name must be this transmission's file name exactly: one name that merely contains another
        (…_1 in …_15) belongs to the longer one.
        """
        self.ensure_one()
        match = re.match(r"^CHA(CO|DE)(\d{7})(.+)\.(XML|PDF|TXT)$", name, re.I)
        if match and match.group(3) == self.file_base:
            return "command" if match.group(1).upper() == "CO" else "request"
        match = re.match(r"^CHAFI[A-Z]{3}_\d+_\d+_(.+)\.ZIP$", name, re.I)
        if match:
            stem = match.group(1)
            # The Ministry may add a digit when a request gives more than one file; the file name ends with a letter,
            # so what is left once that digit is dropped is ours or it is not.
            if stem == self.file_base or (stem[:-1] == self.file_base and stem[-1:].isdigit()):
                return "file"
        return None

    def action_fetch_answers(self):
        """Fetch what the Ministry left for the school, decrypt it, check its signature and read it.

        A file is taken off the server only on a later pass, once it is known to have been read AND saved:
        removing it first lost the answer when the rest of the transaction was rolled back.
        """
        self.ensure_one()
        self._check_office()
        config = self.config_id
        if config.mode != "production":
            raise UserError(_("In simulation the answer comes back as the file is sent."))
        config._check_ready_for("fetch")
        count = 0
        try:
            ring, _school, ministry_fpr = config._keyring()
        except gpg.GpgError as exc:
            raise UserError(_("The keys do not work: %s", exc)) from exc
        try:
            with config._sftp() as client, tempfile.TemporaryDirectory(prefix="bfc") as tmp:
                out_dir = config.sudo().sftp_out_dir.strip("/")
                read = {a.name for a in self.answer_ids if a.kind != "error"}
                tried = {a.name for a in self.answer_ids}
                for name in client.listdir(out_dir):
                    kind = self._answer_kind(name)
                    if not kind:
                        continue
                    if name in read:
                        client.remove("%s/%s" % (out_dir, name))  # saved on an earlier pass: the server lets it go
                        continue
                    if name in tried:
                        continue  # could not be read before: left on the server, not read again
                    path = os.path.join(tmp, "in")
                    client.get("%s/%s" % (out_dir, name), path)
                    with open(path, "rb") as handle:
                        data = handle.read()
                    try:
                        with self.env.cr.savepoint():
                            plain, signature_ok, _by = ring.decrypt_and_verify(data, signer=ministry_fpr)
                            if not signature_ok:
                                raise UserError(_("The answer %s is not signed by the Ministry's key: it is not read.", name))
                            self._ingest(name, plain, kind)
                            count += 1
                    except Exception as exc:  # noqa: BLE001  one file that cannot be read does not lose the others
                        text = exc.args[0] if isinstance(exc, UserError) and exc.args else str(exc)
                        _logger.warning("Charlemagne answer %s of %s not read: %s", name, self.name, text)
                        self.env["bf.school.charlemagne.answer"].sudo().create({
                            "batch_id": self.id, "name": name, "kind": "error", "error": str(text)[:200],
                            "sha256": hashlib.sha256(data).hexdigest()})
        except (sftp.SftpError, gpg.GpgError) as exc:
            raise UserError(_("The answers could not be fetched: %s", exc)) from exc
        finally:
            ring.__exit__()
        return count

    def action_retry_answers(self):
        """Read again the files that could not be read: they are still on the server, and the cause may be gone."""
        self.ensure_one()
        self._check_office()
        if self.state != "sent" or self.mode != "production":
            raise UserError(_("Only a transmission sent to the Ministry has answers to fetch."))
        failed = self.sudo().answer_ids.filtered(lambda a: a.kind == "error")
        self.env["ir.attachment"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "=", self.id), ("name", "in", failed.mapped("name"))]).unlink()
        failed.unlink()
        return self.action_fetch_answers()

    @api.model
    def _cron_fetch_answers(self):
        for batch in self.sudo().search([("state", "=", "sent"), ("mode", "=", "production")], limit=20):
            try:
                with self.env.cr.savepoint():
                    batch.action_fetch_answers()
            except Exception as exc:  # noqa: BLE001  a batch that cannot be fetched is said, the others go on
                _logger.warning("Charlemagne answers of %s not fetched: %s", batch.name, exc)

    def _ingest(self, name, data, kind=None):
        """Read one returned file: a report is parsed, any other file is kept as it came."""
        self.ensure_one()
        batch = self.sudo()
        kind = kind or self._answer_kind(name) or "file"
        report = problem = None
        if kind == "command":
            report = reports.parse_command_report(data)
            if not report:
                problem = _("This report of the command has no command in it.")
        elif kind == "request":
            report = reports.parse_request_report(data)
            known = {tx.ident for tx in batch.transaction_ids}
            if not report["requests"] and not known & set(report["transactions"]):
                problem = _("This report of a request lists no request and none of this transmission's transactions.")
        self.env["bf.school.charlemagne.answer"].sudo().create({
            "batch_id": self.id, "name": name, "kind": "error" if problem else kind,
            "sha256": hashlib.sha256(data).hexdigest(), "error": problem or False})
        attachment = self.env["ir.attachment"].sudo().create({
            "name": name, "raw": data, "res_model": self._name, "res_id": self.id})
        Message = self.env["bf.school.charlemagne.message"].sudo()
        if kind == "file":
            batch.message_post(body=_("A file came back from the Ministry: %s.", name), attachment_ids=attachment.ids)
            return attachment
        if problem:
            # Kept, said, and not counted as the answer to a request: the transmission stays sent.
            Message.create({"batch_id": self.id, "source": "local", "number": "LOCAL-021", "consequence": "S",
                            "text": _("%(name)s was not read: %(why)s Look the transmission up in Charlemagne.",
                                      name=name, why=problem)})
            return attachment
        if kind == "command":
            batch.command_number = report["number"]
            for message in report["messages"]:
                Message.create({"batch_id": self.id, "source": "ministry", "number": message["number"],
                                "consequence": message["consequence"] or False, "text": message["text"]})
            if self._command_refused(report):
                self._refuse()
            return attachment
        by_ident = {tx.ident: tx for tx in batch.transaction_ids}
        for request in report["requests"]:
            for message in request["messages"]:
                Message.create({"batch_id": self.id, "source": "ministry", "number": message["number"],
                                "consequence": message["consequence"] or False, "text": message["text"]})
        listed = set()
        for ident, answer in report["transactions"].items():
            tx = by_ident.get(ident)
            if not tx:
                continue
            listed.add(tx.id)
            outcome = reports.transaction_outcome(answer["messages"])
            for message in answer["messages"]:
                Message.create({"batch_id": self.id, "transaction_id": tx.id, "source": "ministry",
                                "number": message["number"], "consequence": message["consequence"] or False,
                                "text": message["text"]})
            tx.state = OUTCOME_STATE[outcome]
            if outcome in ("accepted", "accepted_message"):
                self._update_ledger(tx)
        self._settle_unlisted(report, listed)
        self._close_if_answered()
        return attachment

    @staticmethod
    def _command_refused(report):
        """The whole command was refused: a message of the command says so (guide 7.1.3), or every request was."""
        return (any(message["consequence"] == "R" for message in report["messages"])
                or (report["refused"] and report["refused"] >= report["requests"] and not report["accepted"]))

    def _refuse(self):
        """The Ministry refused the command: none of its requests was processed (guide 7.1.1 and 7.1.3).

        Nothing was accepted, so the ledger is as it was: a new transmission makes the same transactions again.
        """
        batch = self.sudo()
        if batch.state != "sent":
            return
        batch.transaction_ids.filtered(lambda t: t.state == "ready").write({"state": "rejected"})
        batch.write({"state": "refused", "answered_on": fields.Datetime.now()})
        batch.message_post(body=_("The Ministry refused the whole transmission: none of its requests was processed. "
                                  "Read its messages, fix what they say and start a new transmission."))

    def _settle_unlisted(self, report, listed):
        """Transactions a request report does not list: accepted without message when its counters say so.

        The guide's report carries counters of the transactions accepted without a message; whether it also lists
        them is not stated. A transaction left unlisted is taken as accepted only when the counters add up to it;
        otherwise it stays as it is and the transmission says so.
        """
        batch = self.sudo()
        Message = self.env["bf.school.charlemagne.message"].sudo()
        for request in report["requests"]:
            of_kind = batch.transaction_ids.filtered(lambda t, r=request: t.kind == r["type"])
            unlisted = of_kind.filtered(lambda t: t.state == "ready" and t.id not in listed)
            if not unlisted:
                continue
            counted = request["accepted_clean"] + request["accepted_message"] + request["rejected"]
            listed_clean = len(of_kind.filtered(lambda t: t.id in listed and t.state == "accepted"))
            if counted and len(unlisted) == request["accepted_clean"] - listed_clean:
                for tx in unlisted:
                    tx.state = "accepted"
                    self._update_ledger(tx)
            else:
                Message.create({"batch_id": self.id, "source": "ministry", "number": "LOCAL-020", "consequence": "S",
                                "text": _("The Ministry's report does not say what became of %(count)s %(kind)s "
                                          "transaction(s): look them up in Charlemagne.", count=len(unlisted),
                                          kind=request["type"])})

    def _close_if_answered(self):
        """Answered when each request of the file has had its report."""
        batch = self.sudo()
        done = len(batch.answer_ids.filtered(lambda a: a.kind == "request"))
        if batch.request_count and done >= batch.request_count and batch.state == "sent":
            batch.write({"state": "answered", "answered_on": fields.Datetime.now()})

    def _update_ledger(self, tx):
        """The Ministry accepted this transaction: remember what it now holds."""
        if tx.kind == "FI":
            return
        Ledger = self.env["bf.school.charlemagne.ledger"].sudo()
        entry = Ledger.search([("school_id", "=", self.school_id.id), ("key", "=", tx.key), ("mode", "=", self.mode)], limit=1)
        values = json.loads(tx.ledger_values or "{}")
        if tx.code == "SUP":
            if entry:
                entry.write({"state": "withdrawn", "batch_id": self.id, "updated_on": fields.Datetime.now()})
            return
        if tx.merge:
            merged = json.loads(entry.values) if entry else {}
            for tag, value in values.items():
                if value is None:
                    merged.pop(tag, None)  # an element taken off with an empty tag
                else:
                    merged[tag] = value
            values = merged
        vals = {"values": json.dumps(values, ensure_ascii=False), "digest": Ledger._digest(values), "state": "accepted",
                "batch_id": self.id, "updated_on": fields.Datetime.now()}
        if entry:
            entry.write(vals)
        else:
            Ledger.create(dict(vals, school_id=self.school_id.id, key=tx.key, kind=tx.kind, mode=self.mode,
                               student_id=tx.student_id.id or False))

    def action_back_to_draft(self):
        """Start over from a built or sealed file that was not sent."""
        self._check_office()
        for batch in self:
            if batch.state not in ("built", "sealed"):
                raise UserError(_("Only a file that was not sent goes back to preparation."))
        self.sudo().write({"state": "draft", "xml": False, "xml_sha256": False, "sealed": False, "sealed_name": False,
                           "sealed_sha256": False, "built_on": False, "request_count": 0})
        self.transaction_ids.sudo().filtered(lambda t: t.state == "ready").write({"state": "draft"})
        return True
