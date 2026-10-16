import json
import os
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import hash_sign

#: The file is the school's own: families, teachers and every other staff member have no
#: right on it. The office reads and keeps it.
MANAGER = "bf_school_core.group_school_manager"

#: What a download link is signed for, and for how long it opens.
DOWNLOAD_SCOPE = "bf_school_record_download"
DOWNLOAD_VALIDITY = timedelta(minutes=5)


def safe_archive_name(name, fallback="piece"):
    """A file name that stays inside the folder of the archive it goes in.

    A piece's name is typed by the office: `../../x` in a zip is written outside the folder it is unpacked into.
    Only the last component is kept, with no control character, and never a name made of dots.
    """
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name if name.strip(".") else fallback


def download_url(env, kind, res_id, path):
    """The address of a download, signed for this user, this file and the next minutes.

    🔴 A link to a download that writes the access log, opened by someone who did not ask for it (a link in a
    message, a page), would write lines in the office's name and build the closure zip: the address only opens
    with the signature the office's own button gives it.
    """
    token = hash_sign(env(su=True), DOWNLOAD_SCOPE, [env.uid, kind, res_id], expiration=DOWNLOAD_VALIDITY)
    return "%s?t=%s" % (path, token)


class SchoolRecord(models.Model):
    """The file (dossier) a private school keeps for each student.

    E-9.1, r. 3, s. 7: at least eight pieces. Section 8: a register of enrolment with five
    entries (name, residence address, birth date, language of instruction, holder of parental
    authority for a minor). Both are read from here: the register is a view of the file.
    """

    _name = "bf.school.record"
    _description = "Student's file"
    _inherit = ["mail.thread"]
    _order = "school_id, student_id"
    _rec_name = "student_id"

    student_id = fields.Many2one(
        "res.partner", "Student", required=True, ondelete="restrict", index=True, tracking=True,
        domain=[("is_student", "=", True)])
    school_id = fields.Many2one("bf.school", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one(related="school_id.company_id", store=True)
    state = fields.Selection(
        [("open", "Open"), ("left", "Left")], default="open", required=True, tracking=True,
        help="A file is kept after the student leaves.")
    teaching_language = fields.Selection(
        [("fr", "French"), ("en", "English"), ("other", "Other")], "Language of instruction",
        default="fr", required=True, tracking=True,
        help="Register of enrolment, entry 4 (E-9.1, r. 3, s. 8).")
    arrival_date = fields.Date("Admitted on", default=fields.Date.context_today)
    departure_date = fields.Date("Left on", copy=False, tracking=True)
    document_ids = fields.One2many("bf.school.record.document", "record_id", "Pieces")
    missing_type_ids = fields.Many2many(
        "bf.school.record.type", string="Missing pieces", compute="_compute_completeness")
    missing_count = fields.Integer("Pieces missing", compute="_compute_completeness", search="_search_missing_count")
    is_complete = fields.Boolean("Complete", compute="_compute_completeness")
    transfer_ids = fields.One2many("bf.school.record.transfer", "record_id", "Transfers")
    log_ids = fields.One2many("bf.school.record.log", "record_id", "Access log")

    # The register of enrolment (s. 8): read from the student's card and family links.
    birthdate = fields.Date(related="student_id.student_birthdate", string="Birth date")
    permanent_code = fields.Char(related="student_id.student_permanent_code")
    residence_address = fields.Char("Residence address", compute="_compute_register")
    authority_holders = fields.Char("Holders of parental authority", compute="_compute_register")

    _sql_constraints = [
        ("student_school_unique", "UNIQUE(student_id, school_id)", "A student has one file per school."),
    ]

    # --- Reading -----------------------------------------------------------

    @api.depends("document_ids.state", "document_ids.type_id", "teaching_language", "company_id")
    def _compute_completeness(self):
        Type = self.env["bf.school.record.type"]
        # Read in sudo: what a file lacks does not depend on who reads it, nor on the other company's own kinds.
        all_types = Type.sudo().search([("requirement", "=", "always")])
        english = self.env.ref("bf_school_record.type_english_eligibility", raise_if_not_found=False)
        for record in self:
            types = all_types.filtered(lambda t: not t.company_id or t.company_id == record.company_id)
            filed = record.document_ids.filtered(lambda d: d.state in ("received", "not_applicable")).type_id
            flagged = record.document_ids.filtered(lambda d: d.state == "missing").type_id
            # Instruction in English needs the proof of eligibility or the Minister's exemption (r. 3, s. 7, para. 5).
            expected = types | (english if english and record.teaching_language == "en" else Type)
            missing = (expected | flagged) - filed
            record.missing_type_ids = missing
            record.missing_count = len(missing)
            record.is_complete = not missing

    def _search_missing_count(self, operator, value):
        """Searchable without being stored: the required kinds can change after a file is filled in."""
        import operator as op
        compare = {"=": op.eq, "!=": op.ne, ">": op.gt, ">=": op.ge, "<": op.lt, "<=": op.le}.get(operator)
        if compare is None:
            raise UserError(_("This filter does not support the operator %s.", operator))
        records = self.with_context(active_test=False).search([])
        return [("id", "in", records.filtered(lambda r: compare(r.missing_count, value)).ids)]

    @api.depends("student_id.street", "student_id.city", "student_id.zip",
                 "student_id.student_guardian_link_ids.has_parental_authority",
                 "student_id.student_guardian_link_ids.lives_with")
    def _compute_register(self):
        for record in self:
            student = record.student_id.sudo()
            links = student.student_guardian_link_ids
            holders = links.filtered("has_parental_authority")
            record.authority_holders = ", ".join(holders.mapped("guardian_id.name"))
            # The student's own address when the card has one, else that of the holder the
            # student lives with.
            source = student if (student.street or student.city) else (
                holders.filtered("lives_with")[:1].guardian_id or holders[:1].guardian_id)
            parts = [source.street, source.city, source.zip] if source else []
            record.residence_address = ", ".join(p for p in parts if p)

    # --- Life of the file ----------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        self._check_manager()
        records = super().create(vals_list)
        records._log("created")
        return records

    def write(self, vals):
        self._check_manager()
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            "A student's file is not deleted: the regulation makes the school keep it, and hand "
            "it to the Minister if the school closes. Close it with the student's departure."))

    @api.model
    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration keeps the students' files."))

    def action_leave(self):
        """The student leaves: the file stays, the notice of departure is asked for and the enrolments end.

        🔴 The gradebook, the ranks, the report cards and the declaration to the Ministry read the enrolment: a file
        that said "left" over an enrolment that said "enrolled" kept the student in every average and rank.
        """
        departure = self.env.ref("bf_school_record.type_departure_notice", raise_if_not_found=False)
        for record in self.filtered(lambda r: r.state == "open"):
            record.write({"state": "left", "departure_date": fields.Date.context_today(record)})
            if departure and not record.document_ids.filtered(lambda d: d.type_id == departure):
                self.env["bf.school.record.document"].create({
                    "record_id": record.id, "type_id": departure.id, "state": "missing",
                    "note": _("Ask for or write the notice of departure.")})
            self.env["bf.school.enrollment"].search([
                ("student_id", "=", record.student_id.id), ("school_id", "=", record.school_id.id),
                ("state", "=", "active")]).action_leave()
            record._log("left")
        return True

    def action_reopen(self):
        self.write({"state": "open", "departure_date": False})
        # The notice of departure that was asked for is no longer wanted: a file that is back is not "incomplete"
        # for a notice nobody will write. One that was received stays.
        departure = self.env.ref("bf_school_record.type_departure_notice", raise_if_not_found=False)
        if departure:
            self.document_ids.filtered(lambda d: d.type_id == departure and d.state == "missing").unlink()
        self._log("reopened")
        return True

    # --- Log -----------------------------------------------------------------

    def _log(self, action, note=None):
        """One line of the access log: who did what to this file, and when. Written by the system."""
        for record in self:
            self.env["bf.school.record.log"].sudo().create({
                "record_id": record.id, "user_id": self.env.user.id, "action": action, "note": note or False})

    # --- What the file holds, for the exports -------------------------------

    def _identity_payload(self):
        """The register entries and what identifies the student. No gender: the school keeps none."""
        self.ensure_one()
        student = self.student_id.sudo()
        return {
            "name": student.name,
            "birth_date": student.student_birthdate and str(student.student_birthdate) or None,
            "permanent_code": student.student_permanent_code or None,
            "residence_address": self.residence_address or None,
            "language_of_instruction": self.teaching_language,
            "holders_of_parental_authority": self.authority_holders or None,
            "school": self.school_id.name,
            "admitted_on": self.arrival_date and str(self.arrival_date) or None,
            "left_on": self.departure_date and str(self.departure_date) or None,
        }

    def _guardians_payload(self):
        self.ensure_one()
        return [{
            "name": link.guardian_id.name, "relationship": link.relationship,
            "parental_authority": link.has_parental_authority, "email": link.guardian_id.email or None,
            "phone": link.guardian_id.phone or None, "lives_with": link.lives_with,
        } for link in self.student_id.sudo().student_guardian_link_ids]

    def _documents_payload(self, with_files=False):
        self.ensure_one()
        out = []
        for doc in self.document_ids:
            item = {"type": doc.type_id.name, "state": doc.state, "date": doc.date and str(doc.date) or None,
                    "note": doc.note or None, "file_name": doc.file_name or None}
            out.append(item)
        return out

    def _extra_payload(self):
        """What other modules of the suite add to an export (results, health...). Extended, not called."""
        self.ensure_one()
        return {}

    def _portable_payload(self):
        """Everything the school holds about the student, as structured data (portability).

        P-39.1 s. 27 para. 3 and A-2.1 s. 84 para. 3: a copy in a structured and commonly used
        technological format.
        """
        self.ensure_one()
        payload = {
            "format": "symbifox-school-record/1",
            "identity": self._identity_payload(),
            "guardians": self._guardians_payload(),
            "pieces": self._documents_payload(),
        }
        payload.update(self._extra_payload())
        return payload

    def action_export_portable(self):
        """A JSON copy of the file for the person it concerns: built and logged when downloaded, never kept."""
        self.ensure_one()
        self._check_manager()
        return {"type": "ir.actions.act_url", "target": "self", "url": download_url(
            self.env, "portable", self.id, "/school/record/%s/portable.json" % self.id)}

    def _portable_bytes(self):
        self.ensure_one()
        self._check_manager()
        data = json.dumps(self._portable_payload(), ensure_ascii=False, indent=2, sort_keys=True).encode()
        self._log("export_portable")
        return data


class SchoolRecordDocument(models.Model):
    """One piece of a student's file."""

    _name = "bf.school.record.document"
    _description = "Piece of a student's file"
    _order = "record_id, type_id, id"

    record_id = fields.Many2one("bf.school.record", required=True, ondelete="cascade", index=True)
    student_id = fields.Many2one(related="record_id.student_id", store=True, index=True)
    school_id = fields.Many2one(related="record_id.school_id", store=True)
    company_id = fields.Many2one(related="record_id.company_id", store=True)
    type_id = fields.Many2one("bf.school.record.type", "Kind", required=True, ondelete="restrict")
    state = fields.Selection(
        [("received", "Received"), ("missing", "Missing"), ("not_applicable", "Not applicable")],
        default="received", required=True)
    date = fields.Date(default=fields.Date.context_today)
    note = fields.Char()
    file = fields.Binary("Document", attachment=True, groups=MANAGER)
    file_name = fields.Char()
    #: What `ir.attachment` does not change from outside: the file under this field (see `ir_attachment.py`).
    _school_sealed_fields = ("file",)
    source_model = fields.Char(copy=False, help="The record of another module this piece comes from.")
    source_res_id = fields.Integer("Source record", copy=False)

    @api.constrains("type_id", "state", "note")
    def _check_state(self):
        for doc in self:
            if doc.state == "not_applicable":
                if doc.type_id.requirement == "always":
                    raise ValidationError(_(
                        "“%s” is always required: it cannot be marked not applicable.", doc.type_id.name))
                if not doc.note:
                    raise ValidationError(_("Say why “%s” does not apply.", doc.type_id.name))

    @api.constrains("state", "file")
    def _check_received_file_or_source(self):
        # A received piece is either a file, or a record of the suite it points to, or noted.
        for doc in self:
            if doc.state == "received" and not (doc.sudo().file or doc.source_model or doc.note):
                raise ValidationError(_(
                    "Attach the file of “%s”, or note where the school keeps it.", doc.type_id.name))

    @api.model_create_multi
    def create(self, vals_list):
        self.env["bf.school.record"]._check_manager()
        docs = super().create(vals_list)
        for doc in docs:
            doc.record_id._log("piece_added", doc.type_id.name)
        return docs

    def write(self, vals):
        self.env["bf.school.record"]._check_manager()
        if "file" in vals:
            for doc in self:
                doc.record_id._log("piece_replaced", doc.type_id.name)
        return super().write(vals)

    def unlink(self):
        self.env["bf.school.record"]._check_manager()
        for doc in self:
            doc.record_id._log("piece_removed", doc.type_id.name)
        return super().unlink()

    def action_download(self):
        self.ensure_one()
        self.env["bf.school.record"]._check_manager()
        return {"type": "ir.actions.act_url", "target": "self",
                "url": download_url(self.env, "document", self.id, "/school/record/document/%s" % self.id)}

    def _school_log_download(self, field_name):
        """Called when the file leaves by the standard download routes (`/web/content`): the log hears of it.

        Returns True when the file must not be given out (never, for a piece: it carries no seal of its own).
        """
        if field_name == "file":
            for doc in self:
                doc.record_id._log("piece_downloaded", doc.type_id.name)
        return False
