# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
import base64
import binascii
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

METHODS = ["OPTIONS", "GET", "HEAD", "PUT", "DELETE", "PROPFIND", "PROPPATCH", "REPORT",
           "MKCALENDAR", "MKCOL", "MOVE", "COPY", "POST", "LOCK", "UNLOCK"]
MAX_BODY = 3 * 1024 * 1024


def _credentials():
    header = request.httprequest.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None, None
    login, _sep, key = decoded.partition(":")
    return login.strip(), key.strip()


def _response(status, headers, body):
    return request.make_response(body, headers=list((headers or {}).items()), status=status)


class DavController(http.Controller):

    @http.route(["/.well-known/caldav", "/.well-known/carddav"], type="http", auth="none",
                methods=METHODS, csrf=False, save_session=False)
    def well_known(self, **kw):
        # RFC 6764: send the client to the context path, where it will authenticate.
        return _response(301, {"Location": "/dav/"}, b"")

    @http.route(["/dav", "/dav/", "/dav/<path:subpath>"], type="http", auth="none", methods=METHODS,
                csrf=False, save_session=False, readonly=False)
    def dav(self, subpath=None, **kw):
        httpreq = request.httprequest
        method = httpreq.method.upper()
        from ..models.dav_service import ALLOW, DAV_HEADER
        base = {"DAV": DAV_HEADER, "Allow": ALLOW}
        if method == "OPTIONS" and "Authorization" not in httpreq.headers:
            return _response(200, base, b"")
        login, key = _credentials()
        remote = httpreq.remote_addr or ""
        uid = request.env["res.users"].sudo()._bf_dav_authenticate(login, key, remote) if login else None
        if not uid:
            hdrs = dict(base, **{"WWW-Authenticate": 'Basic realm="Symbifox", charset="UTF-8"'})
            return _response(401, hdrs, b"")
        if httpreq.content_length and httpreq.content_length > MAX_BODY:
            return _response(413, base, b"")
        body = httpreq.get_data(cache=False) if method in ("PUT", "PROPFIND", "PROPPATCH", "REPORT",
                                                            "MKCALENDAR", "POST") else b""
        if len(body) > MAX_BODY:
            return _response(413, base, b"")
        user = request.env["res.users"].sudo().browse(uid)
        request.update_env(user=uid, context=dict(request.env.context, lang=user.lang or "en_US",
                                                  tz=user.tz or "UTC"))
        headers = {k: v for k, v in httpreq.headers.items()}
        status, hdrs, data = request.env["bf.dav.service"]._handle(method, httpreq.path, headers, body)
        return _response(status, hdrs, data)
