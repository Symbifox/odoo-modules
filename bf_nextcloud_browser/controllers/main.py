"""Binary streaming for the Nextcloud browser (download + inline preview).

orm.call cannot stream binary, so file bytes are served here. Access control and
path scoping are delegated to bf.nc.browser._resolve_path, which re-derives the
folder root from the record (never trusting the client path) and checks the group.
"""

import logging
import mimetypes
import posixpath
from urllib.parse import quote

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..models.nc_identity import NcAccountUnavailable, NcNotConnected, NcTokenRejected

_logger = logging.getLogger(__name__)


# The only types previewed inline as themselves. An allowlist, not a list of
# dangerous types: a browser renders many XML-based types as documents (Atom,
# RDF, MathML, XSLT...), the host's /etc/mime.types names some 150 of them, and
# any of those shown from the Odoo origin could draw a fake sign-in page or send
# the reader elsewhere, scripts forbidden or not.
INLINE_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm", "audio/flac",
    "video/mp4", "video/webm", "video/ogg",
    "text/plain",
}
# Readable as source text. Everything else that is not inline is downloaded.
TEXT_TYPES = {
    "application/json", "application/xml", "application/javascript",
    "application/x-sh", "application/x-yaml", "application/yaml", "application/toml",
    "application/sql", "application/x-python",
}


def preview_response(filename, as_attachment):
    """(Content-Type, disposition, sandboxed) for a file served to the browser."""
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if as_attachment:
        return ctype, "attachment", False
    if ctype in INLINE_TYPES:
        # The browser's PDF viewer does not run in a sandboxed document.
        return ctype, "inline", ctype != "application/pdf"
    if ctype.startswith("text/") or ctype.endswith(("+xml", "+json")) or ctype in TEXT_TYPES:
        return "text/plain; charset=utf-8", "inline", True
    return ctype, "attachment", False


class BfNcBrowserController(http.Controller):

    def _stream(self, config, abs_path, as_attachment):
        content = config._webdav_get(abs_path)
        filename = posixpath.basename(abs_path) or "fichier"
        ctype, disposition, sandboxed = preview_response(filename, as_attachment)
        headers = [
            ("Content-Type", ctype),
            ("Content-Length", str(len(content))),
            (
                "Content-Disposition",
                "%s; filename*=UTF-8''%s" % (disposition, quote(filename)),
            ),
            # Never sniff a text/* file into executable HTML.
            ("X-Content-Type-Options", "nosniff"),
        ]
        if disposition == "inline":
            # Inline preview is served same-origin as Odoo. Scripts, forms and
            # <base> are forbidden for every type; everything but the PDF viewer
            # is also sandboxed into an opaque origin.
            policy = "script-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
            if sandboxed:
                policy = "sandbox; " + policy
            headers.append(("Content-Security-Policy", policy))
        return request.make_response(content, headers=headers)

    def _stream_as_person(self, config, abs_path, as_attachment):
        """Stream, turning a per-person refusal into a readable answer.

        The preview opens in a dialog: a bare 404 there reads as "the file is
        gone" when the truth is "connect your Nextcloud" or "your connection
        was revoked".
        """
        try:
            return self._stream(config, abs_path, as_attachment)
        except (NcNotConnected, NcTokenRejected, NcAccountUnavailable) as e:
            return request.make_response(
                str(e),
                headers=[("Content-Type", "text/plain; charset=utf-8")],
                status=403,
            )
        except UserError:
            return request.not_found()

    def _serve(self, model, res_id, rel_path, as_attachment):
        if not model or not res_id:
            return request.not_found()
        browser = request.env["bf.nc.browser"]
        try:
            config, abs_path, _root = browser._resolve_path(
                model, int(res_id), rel_path or ""
            )
        except (AccessError, UserError, ValidationError, ValueError):
            return request.not_found()
        return self._stream_as_person(config, abs_path, as_attachment)

    def _serve_root(self, rel_path, as_attachment):
        browser = request.env["bf.nc.browser"]
        try:
            config, abs_path, _root = browser._resolve_path_standalone(rel_path or "")
        except (AccessError, UserError, ValidationError, ValueError):
            return request.not_found()
        return self._stream_as_person(config, abs_path, as_attachment)

    @http.route("/bf_nc_browser/download", type="http", auth="user")
    def download(self, model=None, res_id=None, rel_path="", **kw):
        return self._serve(model, res_id, rel_path, as_attachment=True)

    @http.route("/bf_nc_browser/preview", type="http", auth="user")
    def preview(self, model=None, res_id=None, rel_path="", **kw):
        return self._serve(model, res_id, rel_path, as_attachment=False)

    @http.route("/bf_nc_browser/root_download", type="http", auth="user")
    def root_download(self, rel_path="", **kw):
        return self._serve_root(rel_path, as_attachment=True)

    @http.route("/bf_nc_browser/root_preview", type="http", auth="user")
    def root_preview(self, rel_path="", **kw):
        return self._serve_root(rel_path, as_attachment=False)
