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

_logger = logging.getLogger(__name__)


class BfNcBrowserController(http.Controller):

    def _stream(self, config, abs_path, as_attachment):
        content = config._webdav_get(abs_path)
        filename = posixpath.basename(abs_path) or "fichier"
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        disposition = "attachment" if as_attachment else "inline"
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
        if not as_attachment:
            # Inline preview is served same-origin as Odoo; neutralise any
            # script in a malicious .html/.svg so it cannot run in our origin.
            # script-src 'none' blocks scripts without breaking PDF/image/text.
            headers.append(
                ("Content-Security-Policy", "script-src 'none'; frame-ancestors 'self'")
            )
        return request.make_response(content, headers=headers)

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
        return self._stream(config, abs_path, as_attachment)

    def _serve_root(self, rel_path, as_attachment):
        browser = request.env["bf.nc.browser"]
        try:
            config, abs_path, _root = browser._resolve_path_standalone(rel_path or "")
        except (AccessError, UserError, ValidationError, ValueError):
            return request.not_found()
        return self._stream(config, abs_path, as_attachment)

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
