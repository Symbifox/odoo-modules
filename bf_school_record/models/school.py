import base64
import csv
import hashlib
import io
import json
import zipfile

from odoo import _, fields, models
from odoo.exceptions import AccessError

from .record import MANAGER, download_url, safe_archive_name

#: What a spreadsheet takes for a formula when a cell starts with it.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value):
    """A cell of the register that no spreadsheet runs: the address and the names come from what the families type."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


class School(models.Model):
    _inherit = "bf.school"

    record_transfer_days = fields.Integer(
        "Transfer target (days)", default=5,
        help="Days within which the school sends a leaving student's information to the receiving "
             "school. The Private Education Act (s. 38.1) says as soon as possible and gives no "
             "figure: this is the school's own target, used to flag a late transfer.")
    record_count = fields.Integer("Files", compute="_compute_record_count")

    def _compute_record_count(self):
        Record = self.env["bf.school.record"]
        can_read = Record.has_access("read")
        for school in self:
            school.record_count = Record.search_count([("school_id", "=", school.id)]) if can_read else 0

    def action_create_missing_records(self):
        """A file for every student enrolled who has none (before this module was installed)."""
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration opens the students' files."))
        enrollments = self.env["bf.school.enrollment"].search([("school_id", "=", self.id)])
        enrollments._school_ensure_record()
        return True

    def action_view_records(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Students' files"), "res_model": "bf.school.record",
            "view_mode": "list,form", "domain": [("school_id", "=", self.id)],
            "context": {"default_school_id": self.id}}

    def action_export_closure(self):
        """Every file of the school in one zip, built when it is downloaded and never kept.

        E-9.1, r. 3, s. 7 and 8: on closure the school hands its students' files over to the Minister.
        🔴 It used to be stored as an attachment of the school, which every teacher can read: the zip holds the
        birth certificates. It is built by a route the office alone can call, and nothing stays behind.
        """
        self.ensure_one()
        self._check_closure_access()
        return {"type": "ir.actions.act_url", "target": "self",
                "url": download_url(self.env, "closure", self.id, "/school/record/closure/%s" % self.id)}

    def _check_closure_access(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only the school administration exports the files."))

    def _closure_zip(self, handle):
        """Write the closure zip to `handle` (a file object), one piece at a time: never the whole school in memory."""
        self.ensure_one()
        self._check_closure_access()
        records = self.env["bf.school.record"].search([("school_id", "=", self.id)])
        manifest = {"school": self.name, "exported_on": str(fields.Date.context_today(self)), "files": {}}
        register = io.StringIO()
        writer = csv.writer(register)
        writer.writerow(["name", "residence_address", "birth_date", "language_of_instruction",
                         "holders_of_parental_authority", "permanent_code", "state"])
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
            def add(name, blob):
                archive.writestr(name, blob)
                manifest["files"][name] = hashlib.sha256(blob).hexdigest()

            for record in records:
                identity = record._identity_payload()
                writer.writerow([csv_cell(value) for value in (
                    identity["name"], identity["residence_address"] or "", identity["birth_date"] or "",
                    identity["language_of_instruction"], identity["holders_of_parental_authority"] or "",
                    identity["permanent_code"] or "", record.state)])
                folder = "files/%s-%s" % (record.student_id.id, record.id)
                add("%s/record.json" % folder, json.dumps(
                    record._portable_payload(), ensure_ascii=False, indent=2, sort_keys=True).encode())
                for doc in record.document_ids.filtered(lambda d: d.state == "received" and d.sudo().file):
                    add("%s/pieces/%s-%s" % (folder, doc.id, safe_archive_name(doc.file_name)),
                        base64.b64decode(doc.sudo().file))
                record._log("closure_export")
            add("register.csv", register.getvalue().encode())
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode())
