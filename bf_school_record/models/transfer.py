import base64
import hashlib
import io
import json
import zipfile
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .record import MANAGER, download_url, safe_archive_name


class SchoolRecordLog(models.Model):
    """Who did what to a file: written by the system, never edited, never deleted."""

    _name = "bf.school.record.log"
    _description = "Access log of a student's file"
    _order = "id desc"

    record_id = fields.Many2one("bf.school.record", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="record_id.company_id", store=True)
    user_id = fields.Many2one("res.users", "Done by", required=True, ondelete="restrict")
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    action = fields.Selection([
        ("created", "File created"), ("left", "Student left"), ("reopened", "File reopened"),
        ("piece_added", "Piece added"), ("piece_replaced", "Piece replaced"),
        ("piece_removed", "Piece removed"), ("piece_downloaded", "Piece downloaded"),
        ("export_portable", "Copy given (portability)"),
        ("transfer_prepared", "Transfer prepared"), ("transfer_sent", "Transfer sent"),
        ("closure_export", "Closure export"), ("transfer_downloaded", "Transfer package downloaded"),
        ("seal_mismatch", "File refused: it no longer matches its seal")], required=True)
    note = fields.Char()

    def write(self, vals):
        raise UserError(_("The access log is not edited."))

    def unlink(self):
        raise UserError(_("The access log is not deleted."))


class SchoolRecordTransfer(models.Model):
    """A student's information sent to the school that receives them.

    Law 23 (L.Q. 2023, c. 32) added s. 38.1 to the Private Education Act: the school that
    loses a student sends the new school, as soon as possible, the information it needs. The
    law names no figure, so the school sets its own target; what is sent is chosen here, and
    the package carries a manifest with the SHA-256 of every file.
    """

    _name = "bf.school.record.transfer"
    _description = "Transfer of a student's information to another school"
    _inherit = ["mail.thread"]
    _order = "requested_on desc, id desc"

    record_id = fields.Many2one("bf.school.record", required=True, ondelete="restrict", index=True)
    student_id = fields.Many2one(related="record_id.student_id", store=True)
    school_id = fields.Many2one(related="record_id.school_id", store=True)
    company_id = fields.Many2one(related="record_id.company_id", store=True)
    destination_name = fields.Char("Receiving school", required=True, tracking=True)
    destination_code = fields.Char("Ministry code of the receiving school")
    requested_on = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    due_on = fields.Date("Send by", compute="_compute_due_on", store=True,
                         help="The school's own target: the law says as soon as possible.")
    include_guardians = fields.Boolean("The guardians", default=True)
    include_results = fields.Boolean("The results", default=True)
    include_documents = fields.Boolean(
        "The pieces of the file", default=False,
        help="Off by default: the file holds the birth certificate and other documents the "
             "receiving school may not need.")
    scope_note = fields.Text("What was asked for", help="The receiving school's request, in its words.")
    state = fields.Selection(
        [("draft", "To prepare"), ("ready", "Ready"), ("sent", "Sent")],
        default="draft", required=True, tracking=True)
    package = fields.Binary(attachment=True, groups=MANAGER, readonly=True, copy=False)
    package_name = fields.Char(readonly=True, copy=False)
    package_sha256 = fields.Char("SHA-256", readonly=True, copy=False)
    sent_on = fields.Datetime(readonly=True, copy=False)
    sent_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    sent_how = fields.Char(
        "How it was sent", tracking=True,
        help="For example the secure transfer link, or the file handed over. A correction after the sending is "
             "kept in the history of the transfer.")

    @api.depends("requested_on", "school_id.record_transfer_days")
    def _compute_due_on(self):
        for transfer in self:
            transfer.due_on = (
                transfer.requested_on + timedelta(days=transfer.school_id.record_transfer_days)
                if transfer.requested_on else False)

    #: What `ir.attachment` does not change from outside: the file under this field (see `ir_attachment.py`).
    _school_sealed_fields = ("package",)

    # 🔴 State and proof move with their buttons; `readonly` does not stop an RPC write.
    _GUARDED = ("state", "package", "package_name", "package_sha256", "sent_on", "sent_by_id", "due_on")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # An empty value forges nothing: the web client sends the screen's defaults as they are.
            forbidden = {f for f in (set(vals) & set(self._GUARDED)) - {"state"} if vals[f]}
            if (forbidden or vals.get("state", "draft") != "draft") and not self.env.su:
                raise AccessError(_("A transfer's state and package move with the buttons."))
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) & set(self._GUARDED) and not self.env.su:
            raise AccessError(_("A transfer's state and package move with the buttons."))
        for transfer in self:
            if transfer.state == "sent" and set(vals) - {"sent_how"}:
                raise UserError(_("A transfer that was sent is kept as it went."))
            if transfer.state == "ready" and set(vals) - {"sent_how"} and not self.env.su:
                # 🔴 The package was built for this destination and this choice: change them, prepare again.
                raise UserError(_("A prepared transfer is not edited: the package was made for what it says."))
        return super().write(vals)

    def unlink(self):
        if any(t.state != "draft" for t in self):
            raise UserError(_("A prepared or sent transfer is kept: it is part of the file's history."))
        return super().unlink()

    # --- Building the package -----------------------------------------------------------------

    def _build_package(self):
        """The zip of this transfer as it would be made now: (bytes, SHA-256).

        The same data gives the same bytes, so the package kept when it was prepared can be compared with
        what the file says today."""
        self.ensure_one()
        record = self.record_id
        data = {
            "format": "symbifox-school-transfer/1",
            "destination": {"name": self.destination_name, "code": self.destination_code or None},
            "requested_on": str(self.requested_on),
            "identity": record._identity_payload(),
        }
        if self.include_guardians:
            data["guardians"] = record._guardians_payload()
        if self.include_results:
            data.update(record._transfer_results_payload())
        files = {"transfer.json": json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode()}
        if self.include_documents:
            for doc in record.document_ids.filtered(lambda d: d.state == "received" and d.sudo().file):
                name = "pieces/%s-%s" % (doc.id, safe_archive_name(doc.file_name))
                files[name] = base64.b64decode(doc.sudo().file)
        manifest = {"files": {name: hashlib.sha256(blob).hexdigest() for name, blob in sorted(files.items())}}
        files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, blob in sorted(files.items()):
                # A fixed date: preparing the same transfer twice gives the same package and the same SHA-256.
                info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, blob)
        blob = buffer.getvalue()
        return blob, hashlib.sha256(blob).hexdigest()

    def action_prepare(self):
        """Build the package: a zip with the data, the chosen pieces and a manifest."""
        for transfer in self:
            if transfer.state == "sent":
                raise UserError(_("This transfer was already sent."))
            blob, digest = transfer._build_package()
            transfer.sudo().write({
                "package": base64.b64encode(blob), "package_name": "transfer-%s-%s.zip" % (transfer.id, transfer.student_id.id),
                "package_sha256": digest, "state": "ready"})
            transfer.record_id._log("transfer_prepared", transfer.destination_name)
        return True

    def action_back_to_draft(self):
        """Change what a prepared transfer says: the package goes, and is made again."""
        for transfer in self:
            if transfer.state != "ready":
                raise UserError(_("Only a prepared transfer goes back to being prepared."))
            transfer.sudo().write({"state": "draft", "package": False, "package_name": False, "package_sha256": False})
        return True

    def action_download(self):
        self.ensure_one()
        self.env["bf.school.record"]._check_manager()
        return {"type": "ir.actions.act_url", "target": "self",
                "url": download_url(self.env, "transfer", self.id, "/school/record/transfer/%s" % self.id)}

    def _package_matches_seal(self):
        """Is the package kept still the one whose SHA-256 was written when it was prepared?"""
        self.ensure_one()
        package = self.sudo().package
        return bool(package and self.package_sha256
                    and hashlib.sha256(base64.b64decode(package)).hexdigest() == self.package_sha256)

    def _school_log_download(self, field_name):
        """Called when the package leaves by the standard download routes (`/web/content`): the log hears of it.

        Returns True when the package must not be given out: it no longer matches its SHA-256 (the line says so).
        """
        refused = False
        if field_name == "package":
            for transfer in self:
                if transfer._package_matches_seal():
                    transfer.record_id._log("transfer_downloaded", transfer.destination_name)
                else:
                    transfer.record_id._log("seal_mismatch", transfer.destination_name)
                    refused = True
        return refused

    def action_mark_sent(self):
        for transfer in self:
            if transfer.state != "ready":
                raise UserError(_("Prepare the package before saying it was sent."))
            if not transfer.sent_how:
                raise UserError(_("Say how it was sent."))
            # The package is a snapshot: a report card replaced, a name or an address corrected since it was
            # prepared would send the receiving school what the file no longer says.
            if transfer.package_sha256 and transfer._build_package()[1] != transfer.package_sha256:
                raise UserError(_(
                    "What this transfer holds changed since the package was prepared (a report card, a "
                    "name, a piece). Use “Change what it holds” and prepare it again."))
            transfer.sudo().write({"state": "sent", "sent_on": fields.Datetime.now(), "sent_by_id": self.env.user.id})
            transfer.record_id._log("transfer_sent", transfer.destination_name)
        return True


class SchoolRecordResults(models.Model):
    _inherit = "bf.school.record"

    def _transfer_results_payload(self):
        """The results that go in a transfer. Extended by the report card module."""
        self.ensure_one()
        return {}
