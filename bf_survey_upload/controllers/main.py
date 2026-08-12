import base64
import logging
import mimetypes

from werkzeug.utils import secure_filename

from odoo import _, http
from odoo.http import request
from odoo.addons.survey.controllers.main import Survey

_logger = logging.getLogger(__name__)

# Hard caps to bound abuse on a public endpoint.
# Per-question max size is configurable via survey.question.file_upload_max_size_mb,
# but we still enforce request-level and aggregate caps to prevent DoS.
MAX_FILES_PER_REQUEST = 10
MAX_TOTAL_BYTES_PER_ANSWER = 200 * 1024 * 1024  # 200 MB cumulative across all uploads
ABSOLUTE_MAX_FILE_BYTES = 100 * 1024 * 1024  # 100 MB hard ceiling regardless of config

# Always denied, even when the per-question whitelist is empty. Prevents stored
# XSS via served-as-HTML attachments, server-side execution if a future proxy
# misroutes the file, and obvious executables. Pure XML formats (xml/xsl/xslt)
# are NOT denied because they have legitimate data-export uses and the
# server-derived mimetype prevents browser execution.
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


class BfSurveyUpload(Survey):

    # csrf=False is safe here because:
    #   - The route requires <answer_token>, a uuid4 (122 bits of entropy) generated
    #     by survey.user_input and never exposed cross-origin by Odoo.
    #   - _get_access_data(..., ensure_token=True) enforces token validity on every call.
    #   - A cross-origin attacker without the token cannot forge a valid request.
    # multipart/form-data uploads cannot trivially carry an Odoo CSRF token, so
    # token-based auth is the chosen defense.
    @http.route(
        "/survey/upload/<string:survey_token>/<string:answer_token>/<int:question_id>",
        type="http",
        auth="public",
        methods=["POST"],
        website=True,
        csrf=False,
    )
    def bf_survey_upload(self, survey_token, answer_token, question_id, **post):
        access_data = self._get_access_data(survey_token, answer_token, ensure_token=True)
        if access_data["validity_code"] is not True:
            return request.make_json_response(
                {"error": access_data["validity_code"]}, status=403
            )
        answer_sudo = access_data["answer_sudo"]
        if answer_sudo.state == "done":
            return request.make_json_response({"error": "unauthorized"}, status=403)

        question = (
            request.env["survey.question"]
            .sudo()
            .browse(question_id)
            .exists()
        )
        if not question or question.survey_id != answer_sudo.survey_id:
            return request.make_json_response({"error": "invalid_question"}, status=400)
        if question.question_type != "file_upload":
            return request.make_json_response({"error": "wrong_question_type"}, status=400)

        # Pre-check Content-Length to fail fast on oversized requests
        # before reading file bodies into memory. Note: this is advisory only —
        # chunked requests have content_length=None (treated as 0), so the
        # per-file and aggregate caps below are the real enforcement.
        content_length = request.httprequest.content_length or 0
        per_file_max = min(
            (question.file_upload_max_size_mb or 25) * 1024 * 1024,
            ABSOLUTE_MAX_FILE_BYTES,
        )
        request_cap = min(
            per_file_max * MAX_FILES_PER_REQUEST,
            MAX_TOTAL_BYTES_PER_ANSWER,
        )
        if content_length > request_cap + 65536:
            return request.make_json_response(
                {"error": "too_large", "message": _("Requête trop volumineuse.")},
                status=413,
            )

        files = request.httprequest.files.getlist("file")
        if not files:
            return request.make_json_response({"error": "no_file"}, status=400)
        if len(files) > MAX_FILES_PER_REQUEST:
            return request.make_json_response(
                {
                    "error": "too_many_files",
                    "message": _(
                        "Maximum %(n)s fichiers par envoi.",
                        n=MAX_FILES_PER_REQUEST,
                    ),
                },
                status=400,
            )

        # Take a row-level lock on this user_input so concurrent uploads
        # serialize on the aggregate quota check below. Without this, two
        # parallel POSTs could each see existing_total=X and both succeed,
        # exceeding MAX_TOTAL_BYTES_PER_ANSWER.
        request.env.cr.execute(
            "SELECT id FROM survey_user_input WHERE id = %s FOR UPDATE",
            (answer_sudo.id,),
        )

        # Aggregate cap across all uploads already attached to this answer.
        existing_attachments = (
            request.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "survey.user_input"),
                    ("res_id", "=", answer_sudo.id),
                ]
            )
        )
        existing_total = sum(existing_attachments.mapped("file_size"))

        # Per-question cap on the TOTAL number of files (0 = unlimited).
        # Only meaningful when multiple files are allowed; single-file
        # questions are already bounded to 1 by validate_question and the
        # front-end. Checked under the row lock so concurrent uploads cannot
        # race past the limit.
        if question.file_upload_multiple and question.max_file_count > 0:
            incoming = sum(1 for f in files if f and f.filename)
            if len(existing_attachments) + incoming > question.max_file_count:
                return request.make_json_response(
                    {
                        "error": "too_many_files",
                        "message": _(
                            "Vous pouvez téléverser au maximum %(n)s fichier(s) "
                            "pour cette question.",
                            n=question.max_file_count,
                        ),
                    },
                    status=400,
                )

        allowed_exts = question._get_allowed_extensions_list()
        created = []
        running_total = existing_total

        for f in files:
            if not f or not f.filename:
                continue

            # secure_filename strips Unicode (Café.pdf → Caf.pdf), so we use it
            # only to derive a safe extension and reject path-traversal/control
            # characters. The stored attachment name preserves the original.
            stripped = secure_filename(f.filename) or ""
            ext = (
                stripped.rsplit(".", 1)[-1].lower()
                if "." in stripped
                else ""
            )

            # Empty extension is always rejected (avoids ambiguous downloads).
            if not ext:
                return request.make_json_response(
                    {
                        "error": "bad_extension",
                        "message": _("Le fichier doit avoir une extension."),
                    },
                    status=400,
                )

            # Deny-list applies regardless of whitelist (defense in depth).
            if ext in DENY_EXTENSIONS:
                return request.make_json_response(
                    {
                        "error": "bad_extension",
                        "message": _(
                            "Le format « .%(ext)s » n'est pas autorisé pour des raisons de sécurité.",
                            ext=ext,
                        ),
                    },
                    status=400,
                )

            if allowed_exts and ext not in allowed_exts:
                return request.make_json_response(
                    {
                        "error": "bad_extension",
                        "message": _(
                            "Le format « .%(ext)s » n'est pas autorisé. "
                            "Formats acceptés : %(exts)s.",
                            ext=ext or "?",
                            exts=", ".join(allowed_exts),
                        ),
                    },
                    status=400,
                )

            # Preserve the original (Unicode-safe) filename for display, but
            # strip any path component to prevent traversal via name field, drop
            # control chars / RTL-override / line breaks (anti-spoofing and
            # anti log-injection), and cap length.
            raw_name = f.filename.replace("\\", "/").rsplit("/", 1)[-1]
            display_name = "".join(
                c for c in raw_name if c.isprintable() and c not in "\r\n"
            )[:255] or stripped or "file"

            data = f.read()
            if not data:
                return request.make_json_response(
                    {
                        "error": "empty_file",
                        "message": _("Le fichier « %(name)s » est vide.", name=display_name),
                    },
                    status=400,
                )
            if len(data) > per_file_max:
                return request.make_json_response(
                    {
                        "error": "too_large",
                        "message": _(
                            "Le fichier « %(name)s » dépasse la taille maximale de %(mb)s Mo.",
                            name=display_name,
                            mb=per_file_max // (1024 * 1024),
                        ),
                    },
                    status=400,
                )

            running_total += len(data)
            if running_total > MAX_TOTAL_BYTES_PER_ANSWER:
                return request.make_json_response(
                    {
                        "error": "quota_exceeded",
                        "message": _(
                            "Quota total dépassé pour cette réponse au sondage "
                            "(maximum %(mb)s Mo cumulés).",
                            mb=MAX_TOTAL_BYTES_PER_ANSWER // (1024 * 1024),
                        ),
                    },
                    status=400,
                )

            # Server-side mimetype derivation: ignore client-supplied Content-Type
            # to prevent polyglot/XSS via spoofed mimetype.
            server_mimetype = (
                mimetypes.guess_type(display_name)[0] or "application/octet-stream"
            )

            file_size = len(data)
            encoded = base64.b64encode(data)
            del data  # release the raw buffer before create() copies again

            attachment = request.env["ir.attachment"].sudo().create(
                {
                    "name": display_name,
                    "datas": encoded,
                    "mimetype": server_mimetype,
                    "res_model": "survey.user_input",
                    "res_id": answer_sudo.id,
                    "public": False,
                }
            )
            del encoded
            created.append(
                {
                    "id": attachment.id,
                    "name": attachment.name,
                    "size": file_size,
                    "mimetype": attachment.mimetype,
                }
            )

        return request.make_json_response({"attachments": created})

    @http.route(
        "/survey/upload/<string:survey_token>/<string:answer_token>/delete/<int:attachment_id>",
        type="http",
        auth="public",
        methods=["POST"],
        website=True,
        csrf=False,
    )
    def bf_survey_upload_delete(self, survey_token, answer_token, attachment_id, **post):
        access_data = self._get_access_data(survey_token, answer_token, ensure_token=True)
        if access_data["validity_code"] is not True:
            return request.make_json_response(
                {"error": access_data["validity_code"]}, status=403
            )
        answer_sudo = access_data["answer_sudo"]
        if answer_sudo.state == "done":
            return request.make_json_response({"error": "unauthorized"}, status=403)

        attachment = (
            request.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("id", "=", attachment_id),
                    ("res_model", "=", "survey.user_input"),
                    ("res_id", "=", answer_sudo.id),
                ],
                limit=1,
            )
        )
        if not attachment:
            return request.make_json_response({"error": "not_found"}, status=404)
        attachment.unlink()
        return request.make_json_response({"deleted": attachment_id})
