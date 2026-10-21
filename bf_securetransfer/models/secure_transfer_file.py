"""One uploaded file within a transfer.

The browser talks straight to S3 through presigned URLs; this model owns the
per-file upload plan (simple PUT under the multipart threshold, MPU above),
the server-side verification (HEAD size + pinned ETag) and the S3 cleanup.
S3 keys are opaque (``<tenant_prefix>/<transfer_uuid>/<file_uuid>``): no
filename, no PII — the display name only lives in the database and is applied
at download time via response-content-disposition. The tenant prefix
(s3.key_prefix) isolates instances that share one bucket.
"""
import logging
import math
import mimetypes
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import s3

_logger = logging.getLogger(__name__)

# Always denied, regardless of anything else. Verbatim clone of the
# bf_survey_upload deny-list: prevents stored XSS via served-as-HTML
# attachments, server-side execution if a future proxy misroutes the file,
# and obvious executables. Pure XML formats (xml/xsl/xslt) are NOT denied
# because they have legitimate data-export uses and downloads are forced to
# attachment + octet-stream anyway.
DENY_EXTENSIONS = frozenset(
    {
        # Browser-renderable / XSS vectors
        "html", "htm", "xhtml", "svg", "svgz", "mhtml", "mht",
        "js", "mjs", "wasm",
        # Server-side execution (defense in depth — should never reach a handler)
        "php", "php3", "php4", "php5", "php7", "phps", "phtml", "pht",
        "asp", "aspx", "ashx", "cer",
        "jsp", "jspx", "jsv", "jspf",
        "cgi", "pl", "py", "rb", "lua",
        "swf",
        "class", "jar", "war", "ear",
        # Native executables and scripts
        "exe", "bat", "cmd", "com", "ps1", "psm1", "sh", "bash", "zsh",
        "msi", "dll", "scr", "vbs", "vbe", "jse", "wsf", "wsh", "hta",
        "lnk", "reg", "chm",
        # Web server config that could be honored if dropped in served paths
        "htaccess", "htpasswd",
    }
)

_GB, _MB, _KB = 1073741824, 1048576, 1024


def size_label(env, nbytes, limit=False):
    """A byte count for a reader, with its unit in ``env``'s language.

    The units are text, not symbols: French writes « Go », « Mo », « Ko », « o »
    where English writes GB, MB, KB, B. Pin the reader's language on ``env``
    (the visitor's page, the recipient's email). ``limit`` formats an upload
    cap (``%g``, GB or MB only), the default a file size (two decimals in GB,
    one in MB, none in KB, whole bytes below).
    """
    nbytes = nbytes or 0
    if limit:
        if nbytes >= _GB:
            return env._("%s GB", "%g" % (nbytes / float(_GB)))
        return env._("%s MB", "%g" % (nbytes / float(_MB)))
    if nbytes >= _GB:
        return env._("%s GB", "%.2f" % (nbytes / float(_GB)))
    if nbytes >= _MB:
        return env._("%s MB", "%.1f" % (nbytes / float(_MB)))
    if nbytes >= _KB:
        return env._("%s KB", "%.0f" % (nbytes / float(_KB)))
    return env._("%s B", "%d" % nbytes)


class SecureTransferFile(models.Model):
    _name = "secure.transfer.file"
    _description = "Secure transfer: file"
    _order = "id asc"
    _rec_name = "filename"

    transfer_id = fields.Many2one(
        "secure.transfer",
        string="Transfer",
        required=True,
        index=True,
        ondelete="cascade",
    )
    filename = fields.Char(string="File name", required=True)
    extension = fields.Char(string="Extension", readonly=True)
    # Float on purpose: Integer maps to int4 and overflows at 2.1 GB.
    size = fields.Float(string="Declared size (bytes)", required=True)
    size_confirmed = fields.Float(string="Confirmed size (bytes)", readonly=True)
    mimetype = fields.Char(
        string="MIME type",
        readonly=True,
        help="Determined server-side from the name, never supplied by the "
             "client. Informational only: the download forces "
             "application/octet-stream.",
    )
    s3_key = fields.Char(
        string="S3 key",
        required=True,
        readonly=True,
        index=True,
        copy=False,
    )
    etag = fields.Char(
        string="Pinned ETag",
        readonly=True,
        copy=False,
        help="S3 ETag captured at finalization and rechecked before every "
             "download; blocks replacement of the object afterward.",
    )
    checksum = fields.Char(
        string="Checksum",
        readonly=True,
        help="Reserved (Phase 2): SHA-256 computed client-side in a "
             "stream. Empty in the MVP: integrity relies on the exact "
             "size + the pinned ETag.",
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("uploading", "Uploading"),
            ("uploaded", "Uploaded"),
            ("verified", "Verified"),
            ("error", "Error"),
            ("purged", "Purged"),
        ],
        string="Status",
        default="pending",
        required=True,
        copy=False,
    )
    scanned = fields.Selection(
        selection=[
            ("none", "Not scanned"),
            ("pending", "Scan in progress"),
            ("clean", "Clean"),
            ("infected", "Infected"),
        ],
        string="Antivirus scan",
        default="none",
        required=True,
        help="ClamAV hook (Phase 2). The download route only allows \"Not "
             "scanned\" or \"Clean\" for now.",
    )
    download_count = fields.Integer(string="Downloads", default=0, copy=False)

    # -- multipart plan
    upload_mode = fields.Selection(
        selection=[("simple", "Simple PUT"), ("multipart", "Multipart")],
        string="Upload mode",
        required=True,
        default="simple",
        readonly=True,
    )
    s3_upload_id = fields.Char(string="S3 UploadId", readonly=True, copy=False)
    part_size = fields.Integer(string="Part size (bytes)", readonly=True)
    parts_total = fields.Integer(string="Number of parts", readonly=True)
    parts_manifest = fields.Json(
        string="Parts manifest",
        readonly=True,
        copy=False,
        help="Cache of the last ListParts (for resuming). The source of "
             "truth remains ListParts on the S3 side.",
    )

    _sql_constraints = [
        ("s3_key_uniq", "unique(s3_key)", "This S3 key is already in use."),
    ]

    # ------------------------------------------------------------------ guards
    def _check_uploadable(self):
        """Presign/MPU operations are only legal while the parent transfer is
        still a draft — the upload capability dies at finalize."""
        self.ensure_one()
        if self.transfer_id.state != "draft":
            raise UserError(_("This transfer no longer accepts uploads."))

    @staticmethod
    def _key_for(transfer):
        """Opaque S3 key: tenant prefix + transfer prefix + fresh uuid.
        No filename, no PII. The tenant-level root prefix is what isolates
        environments sharing one bucket (see s3.key_prefix)."""
        return "%s/%s/%s" % (
            s3.key_prefix(transfer.env), transfer.s3_prefix, uuid.uuid4().hex,
        )

    @staticmethod
    def _guess_mimetype(filename):
        """Server-side mimetype from the (sanitized) name — informative only,
        the download always forces application/octet-stream."""
        return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"

    def _is_pdf(self):
        """Whether this file can be watermarked. The declared mimetype is
        client-supplied, so the extension is kept as a second chance."""
        self.ensure_one()
        if (self.mimetype or "").lower() == "application/pdf":
            return True
        return (self.filename or "").lower().endswith(".pdf")

    def _size_label(self):
        """The declared size with its unit, in the language of ``self.env``.
        The email templates call it: their rendering already pins the
        recipient's language on the records it reads."""
        self.ensure_one()
        return size_label(self.env, self.size)

    # ------------------------------------------------------------------ simple PUT
    def presign_simple(self):
        """Presigned simple PUT for this file → ``{"url", "headers"}``.
        The signed Content-Length caps the body to the declared size."""
        self._check_uploadable()
        if self.upload_mode != "simple":
            raise UserError(_("This file requires a multipart upload."))
        plan = s3.presign_put(self.env, self.s3_key, int(self.size))
        self.write({"state": "uploading"})
        return plan

    # ------------------------------------------------------------------ multipart
    def _part_size(self):
        return s3._int_param(self.env, "part_size_mb", 16) * 1024 * 1024

    def mpu_initiate(self):
        """Start (or resume) the multipart plan → ``{"upload_id",
        "part_size", "parts_total"}``. Idempotent: a second call returns the
        existing plan so an in-session resume never orphans an MPU."""
        self._check_uploadable()
        if self.upload_mode != "multipart":
            raise UserError(_("This file uploads in a single PUT."))
        if not self.s3_upload_id:
            part_size = self._part_size()
            self.write({
                "s3_upload_id": s3.mpu_initiate(self.env, self.s3_key),
                "part_size": part_size,
                "parts_total": max(1, math.ceil(self.size / part_size)),
                "state": "uploading",
            })
        return {
            "upload_id": self.s3_upload_id,
            "part_size": self.part_size,
            "parts_total": self.parts_total,
        }

    def mpu_sign(self, part_numbers):
        """Presigned UploadPart URLs for the requested part numbers →
        ``{n: url}``. Numbers are clamped to [1, parts_total] and the batch
        is capped (anti presign-farming)."""
        self._check_uploadable()
        if not self.s3_upload_id:
            raise UserError(_("The multipart upload is not initialized."))
        # Require a real sequence. A bare string iterates CHARACTER BY
        # CHARACTER, so mpu_sign("123") used to quietly sign parts 1, 2 and 3.
        # The web controller already rejects non-lists, but this method has no
        # leading underscore — it is RPC surface for anyone holding model
        # access, and the guard belongs where the parsing happens.
        if part_numbers is not None and not isinstance(part_numbers, (list, tuple, set)):
            raise UserError(_("Invalid part numbers."))
        try:
            nums = sorted({int(n) for n in (part_numbers or [])})
        except (TypeError, ValueError):
            raise UserError(_("Invalid part numbers."))
        nums = [n for n in nums if 1 <= n <= self.parts_total]
        if not nums:
            raise UserError(_("No valid part number."))
        batch_max = s3._int_param(self.env, "mpu_sign_batch_max", 20)
        nums = nums[:batch_max]
        return s3.mpu_sign_parts(self.env, self.s3_key, self.s3_upload_id, nums)

    def mpu_status(self):
        """Parts already stored on S3 (ListParts, the source of truth) →
        ``{"parts": [{"n", "etag", "size"}]}``. This is the resume endpoint:
        the client re-uploads only what is missing."""
        self._check_uploadable()
        if not self.s3_upload_id:
            return {"parts": []}
        parts = s3.mpu_list_parts(self.env, self.s3_key, self.s3_upload_id)
        self.write({"parts_manifest": parts})
        return {"parts": parts}

    def mpu_complete(self):
        """Finish the MPU server-side. The part list is rebuilt from
        ListParts (client ETags are never trusted) and the sum of stored
        part sizes must equal the declared size — otherwise the upload is
        aborted and flagged in error. Then HEAD confirms and captures the
        ETag. Returns True."""
        self._check_uploadable()
        if not self.s3_upload_id:
            raise UserError(_("The multipart upload is not initialized."))
        parts = s3.mpu_list_parts(self.env, self.s3_key, self.s3_upload_id)
        stored = sum(p["size"] for p in parts)
        if not parts or stored != int(self.size):
            s3.mpu_abort(self.env, self.s3_key, self.s3_upload_id)
            self.write({
                "state": "error",
                "s3_upload_id": False,
                "parts_manifest": False,
            })
            raise UserError(_(
                "The upload of \"%s\" is incomplete or inconsistent: it "
                "was aborted, please start again.", self.filename,
            ))
        s3.mpu_complete(self.env, self.s3_key, self.s3_upload_id)
        head = s3.head_object(self.env, self.s3_key)
        if not head or head["size"] != int(self.size):
            self.write({"state": "error", "s3_upload_id": False})
            raise UserError(_(
                "The verification of \"%s\" after assembly failed.",
                self.filename,
            ))
        self.write({
            "state": "uploaded",
            "size_confirmed": head["size"],
            "etag": head["etag"],
            "s3_upload_id": False,  # the upload id is dead after Complete
            "parts_manifest": parts,
        })
        return True

    def mpu_abort(self):
        """Abort the in-flight MPU (client gave up on this file). NoSuchUpload
        counts as success. Returns True."""
        self.ensure_one()
        if self.s3_upload_id:
            s3.mpu_abort(self.env, self.s3_key, self.s3_upload_id)
            self.write({
                "s3_upload_id": False,
                "parts_manifest": False,
                "state": "pending",
            })
        return True

    # ------------------------------------------------------------------ download / cleanup
    def presign_download(self):
        """Presigned GET (short TTL, attachment + octet-stream forced)."""
        self.ensure_one()
        return s3.presign_get(self.env, self.s3_key, self.filename, self.mimetype)

    def _verify_on_s3(self):
        """HEAD the object: it must exist with EXACTLY the declared size.
        On success the confirmed size and the ETag are pinned and the file
        goes verified; otherwise it goes error. Idempotent."""
        self.ensure_one()
        head = s3.head_object(self.env, self.s3_key)
        if head is None or head["size"] != int(self.size):
            self.write({"state": "error"})
            return False
        self.write({
            "state": "verified",
            "size_confirmed": head["size"],
            "etag": head["etag"],
        })
        return True

    def _s3_delete(self):
        """Best-effort removal of this file's S3 footprint (abort a pending
        MPU, delete the object). Missing objects count as success. Returns
        True when everything is gone."""
        self.ensure_one()
        ok = True
        if self.s3_upload_id:
            try:
                s3.mpu_abort(self.env, self.s3_key, self.s3_upload_id)
                self.write({"s3_upload_id": False})
            except Exception as e:  # noqa: BLE001 — best-effort, caller decides
                _logger.warning(
                    "bf_securetransfer: abort MPU en échec pour %s : %s",
                    self.s3_key, e,
                )
                ok = False
        failed = s3.delete_keys(self.env, [self.s3_key])
        if failed:
            ok = False
        if ok:
            self.write({"state": "purged"})
        return ok
