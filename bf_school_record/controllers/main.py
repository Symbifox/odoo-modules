import base64
import binascii
import tempfile

from odoo import _, http
from odoo.http import content_disposition, request
from odoo.tools.misc import verify_hash_signed

from ..models.record import DOWNLOAD_SCOPE, MANAGER

#: A download carries the file (or the family's data): nothing in a cache, a proxy or a history.
NO_STORE = ("Cache-Control", "no-store, private")


class SchoolRecordController(http.Controller):
    """Downloads of the file's pieces go through here so that each one is logged.

    🔴 Each route writes the access log, so a GET that anyone could point the office to would write lines in its name:
    an address opens only with the signature its own button gave it (`token`, for this user and this file, a few
    minutes).
    """

    def _manager_only(self):
        if not request.env.user.has_group(MANAGER):
            raise request.not_found()

    def _signed(self, kind, res_id, token):
        """Whether `token` is the signature the button of this user gave for this download; else a plain 404."""
        try:
            values = verify_hash_signed(request.env(su=True), DOWNLOAD_SCOPE, token or "")
        except (ValueError, TypeError, binascii.Error):
            values = None
        if values != [request.env.uid, kind, res_id]:
            raise request.not_found()

    @http.route("/school/record/document/<int:doc_id>", type="http", auth="user")
    def document(self, doc_id, t=None, **kw):
        self._manager_only()
        self._signed("document", doc_id, t)
        doc = request.env["bf.school.record.document"].browse(doc_id).exists()
        if not doc or not doc.sudo().file:
            raise request.not_found()
        doc.record_id._log("piece_downloaded", doc.type_id.name)
        content = base64.b64decode(doc.sudo().file)
        return request.make_response(content, headers=[
            ("Content-Type", "application/octet-stream"),
            ("Content-Disposition", content_disposition(doc.file_name or "piece")),
            ("X-Content-Type-Options", "nosniff"), NO_STORE])

    @http.route("/school/record/transfer/<int:transfer_id>", type="http", auth="user")
    def transfer(self, transfer_id, t=None, **kw):
        self._manager_only()
        self._signed("transfer", transfer_id, t)
        transfer = request.env["bf.school.record.transfer"].browse(transfer_id).exists()
        if not transfer or not transfer.sudo().package:
            raise request.not_found()
        if not transfer._package_matches_seal():
            # 🔴 What was sealed when the package was prepared is what leaves, or nothing does.
            transfer.record_id._log("seal_mismatch", transfer.destination_name)
            return request.make_response(
                _("This package no longer matches its seal: it is not given out."), status=409,
                headers=[("Content-Type", "text/plain; charset=utf-8"), NO_STORE])
        transfer.record_id._log("transfer_downloaded", transfer.destination_name)
        content = base64.b64decode(transfer.sudo().package)
        return request.make_response(content, headers=[
            ("Content-Type", "application/zip"),
            ("Content-Disposition", content_disposition(transfer.package_name or "transfer.zip")),
            ("X-Content-Type-Options", "nosniff"), NO_STORE])

    @http.route("/school/record/<int:record_id>/portable.json", type="http", auth="user")
    def portable(self, record_id, t=None, **kw):
        self._manager_only()
        self._signed("portable", record_id, t)
        record = request.env["bf.school.record"].browse(record_id).exists()
        if not record:
            raise request.not_found()
        return request.make_response(record._portable_bytes(), headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Disposition", content_disposition("%s.json" % (record.student_id.name or "file"))),
            ("X-Content-Type-Options", "nosniff"), NO_STORE])

    @http.route("/school/record/closure/<int:school_id>", type="http", auth="user")
    def closure(self, school_id, t=None, **kw):
        """The closure zip, built into a temporary file as it is read and served from there."""
        self._manager_only()
        self._signed("closure", school_id, t)
        school = request.env["bf.school"].browse(school_id).exists()
        if not school:
            raise request.not_found()
        spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        school._closure_zip(spool)
        size = spool.tell()
        spool.seek(0)
        response = request.make_response(spool.read(), headers=[
            ("Content-Type", "application/zip"),
            ("Content-Disposition", content_disposition("closure-%s.zip" % school.id)),
            ("Content-Length", str(size)), ("X-Content-Type-Options", "nosniff"), NO_STORE])
        return response
