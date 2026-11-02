"""The page where the client uploads their documents: /d/<token>.

The client has no Odoo account, no password, and often nothing but a phone.
Their only proof of identity is having received the link, by text message on
THEIR number or by email in THEIR mailbox: the request token IS the
capability.

⚠️ This token only opens an upload. This page is NEVER given the
``upload_token`` of the underlying draft, because the base's
``/secrets/api/<ut>/finalize`` route accepts to rewrite ``recipient_emails`` on
an ordinary brand: a client holding that token could have their own documents
sent somewhere else. Here, the destination is set when the request is sent, on
the server side, and nothing in this file touches it.

All the heavy plumbing (S3 presigning, quotas, log chain) stays the base's:
these routes only drive it.

Every route speaks the visitor's language, read from Accept-Language like the
base's public pages (``_apply_locale``): the page, the JSON answers and the
error messages a model raises while serving them.
"""

import functools
import hmac
import json
import logging

from markupsafe import Markup

from odoo.exceptions import UserError
from odoo.http import Controller, request, route
from odoo.modules.module import get_manifest

from odoo.addons.bf_securetransfer.controllers.main import (
    _TOKEN_RE,
    _UPLOAD_OPS_MAX,
    _apply_locale,
    _apply_security_headers,
    _client_ip,
    _render_page,
    _s3_connect_origins,
    _token_fail_limiter,
    _TOKEN_FAIL_MAX,
    _upload_ops_limiter,
    _user_agent,
)

_logger = logging.getLogger(__name__)


def _err(code, message):
    return {"error": code, "message": message}


def _guard(func):
    """UserError → safe message; anything else → server log + generic message.

    Same contract as the base: no traceback ever goes out to the public, but a
    useful message does when it is written for the client. The visitor's
    language is set first, so that a model's message is written in it.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        _apply_locale()
        try:
            return func(self, *args, **kwargs)
        except UserError as exc:
            return _err("invalid", str(exc))
        except Exception:
            _logger.exception("bf_document_request: API failure in %s", func.__name__)
            return _err("server_error", request.env._(
                "An error occurred. Please try again later."))
    return wrapper


def _resolve_request(token):
    """Token → request (sudo), or None. Failures are counted (anti-fuzzing)."""
    ip = _client_ip()
    if not _token_fail_limiter.check(ip, _TOKEN_FAIL_MAX):
        _logger.warning("bf_document_request: token failure cap reached for IP %s", ip)
        return None
    if not token or not _TOKEN_RE.match(token):
        _token_fail_limiter.hit(ip)
        return None
    rec = request.env["bf.document.request"].sudo().search(
        [("token", "=", token)], limit=1)
    if not rec or not rec.token or not hmac.compare_digest(rec.token, token):
        _token_fail_limiter.hit(ip)
        return None
    return rec


def _ops_rate_error():
    if not _upload_ops_limiter.consume(_client_ip(), _UPLOAD_OPS_MAX):
        return _err("rate_limited", request.env._(
            "Too many requests. Please try again in a minute."))
    return None


def _open_or_error(token):
    """(open request, None) or (None, error dict)."""
    req = _resolve_request(token)
    if req is None:
        return None, _err("not_found", request.env._("Invalid or expired link."))
    try:
        req._assert_open()
    except UserError as exc:
        return None, _err("closed", str(exc))
    return req, None


def _line_or_error(req, line_id):
    try:
        line_id = int(line_id)
    except (TypeError, ValueError):
        return None, _err("invalid_line", request.env._("Unknown document."))
    line = req.line_ids.filtered(lambda rec: rec.id == line_id)[:1]
    if not line:
        return None, _err("invalid_line", request.env._("Unknown document."))
    return line, None


def _page_strings(env):
    """The page script's strings, in the visitor's language. Built here, not
    in the script: the page is a standalone document without Odoo's web
    client, and the base's page scripts work the same way."""
    return {
        "capture": env._("Take a photo"),
        "choose": env._("Choose a file"),
        # Document already received: the button replaces it, like "Retake".
        "choose_again": env._("Choose another file"),
        "retake": env._("Retake"),
        "uploading": env._("Uploading…"),
        "received": env._("Uploaded"),
        "optional": env._("Optional"),
        "submit": env._("Finish and send"),
        "submitting": env._("Sending…"),
        "done": env._("Thank you, your documents have been sent."),
        "error": env._("An error occurred. Please try again."),
        "too_big": env._("File too large."),
    }


class BfDocumentRequestPortal(Controller):

    # ── The page ──────────────────────────────────────────────────────────────
    @route("/d/<string:token>", type="http", auth="public", methods=["GET"],
           sitemap=False)
    def bf_document_request_page(self, token, **kw):
        locale = _apply_locale()
        env = request.env
        req = _resolve_request(token)
        if req is None:
            brand = env["secure.transfer.brand"].sudo()._from_request()
            visuals = brand._visuals()
            response = _render_page("bf_securetransfer.page_unavailable", {
                "brand": brand, "visuals": visuals, "locale": locale,
                "message": env._("This link is no longer valid."),
            })
            return _apply_security_headers(
                response, img_host=visuals.get("logo_host"))

        brand = req.brand_id.sudo()
        visuals = brand._visuals()
        closed = None
        try:
            req._assert_open()
        except UserError as exc:
            closed = str(exc)

        if closed and req.state != "received":
            response = _render_page("bf_securetransfer.page_unavailable", {
                "brand": brand, "visuals": visuals, "locale": locale,
                "message": closed,
            })
            return _apply_security_headers(
                response, img_host=visuals.get("logo_host"))

        # An opened page is an event of the file. It is logged once per
        # session, otherwise a client who refreshes three times would drown
        # their own chain.
        seen_key = "bf_document_seen_%s" % req.id
        if not closed and not request.session.get(seen_key):
            request.session[seen_key] = True
            req._log(
                "bf_document_request_opened", ip=_client_ip(), ua=_user_agent(),
                actor=req.partner_id.display_name,
                note=req._log_env()._("Upload page opened"))

        config = {
            "token": req.token,
            "lines": [
                {
                    "id": line.id,
                    "name": line.document_type_id.name,
                    "instructions": line.instructions or "",
                    "required": bool(line.required),
                    "state": line.state,
                    "filename": line.file_id.filename or "" if line.file_id else "",
                }
                for line in req.line_ids
            ],
            "strings": _page_strings(env),
        }
        # Markup: QWeb must output the raw JSON in the <script
        # type="application/json"> block, without escaping the quotes. The
        # payload is built entirely here: nothing comes from the client.
        response = _render_page("bf_document_request.page_request", {
            "brand": brand,
            "visuals": visuals,
            "locale": locale,
            "req": req,
            "submitted": req.state == "received",
            # Formatted here, like in the emails: the visitor is anonymous and
            # has no time zone, so the datetime widget would print UTC.
            "deadline_text": req.with_context(lang=env.lang)._email_deadline_text(),
            # <title> is never translated by Odoo: composed here.
            "page_title": env._("Your documents"),
            # The page's script and style sheet are cached for a week: their
            # address changes with each release, otherwise a client who opens
            # a link again after an upgrade runs the former script against the
            # new page.
            "asset_version": get_manifest("bf_document_request")["version"],
            "config_json": Markup(
                json.dumps(config, ensure_ascii=False).replace("</", "<\\/")),
        })
        return _apply_security_headers(
            response,
            s3_host=_s3_connect_origins(env),
            img_host=visuals.get("logo_host"),
        )

    # ── Register a document + presign the PUT ─────────────────────────────────
    @route("/d/<string:token>/presign", type="json", auth="public",
           methods=["POST"])
    @_guard
    def bf_document_presign(self, token, **params):
        error = _ops_rate_error()
        if error:
            return error
        req, error = _open_or_error(token)
        if error:
            return error
        line, error = _line_or_error(req, params.get("line_id"))
        if error:
            return error
        try:
            size = int(params.get("size"))
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            return _err("invalid_size", request.env._("Empty or unreadable file."))
        rec_file = req._register_capture(line, params.get("filename"), size)
        plan = rec_file.sudo().presign_simple()
        return {
            "file_id": rec_file.id,
            "url": plan["url"],
            "headers": plan["headers"],
        }

    # ── The PUT is done: check on the storage ─────────────────────────────────
    @route("/d/<string:token>/uploaded", type="json", auth="public",
           methods=["POST"])
    @_guard
    def bf_document_uploaded(self, token, **params):
        error = _ops_rate_error()
        if error:
            return error
        req, error = _open_or_error(token)
        if error:
            return error
        line, error = _line_or_error(req, params.get("line_id"))
        if error:
            return error
        req._confirm_capture(line, ip=_client_ip(), ua=_user_agent())
        return {"ok": True, "state": line.state}

    # ── The client submits ────────────────────────────────────────────────────
    @route("/d/<string:token>/submit", type="json", auth="public",
           methods=["POST"])
    @_guard
    def bf_document_submit(self, token, **params):
        req, error = _open_or_error(token)
        if error:
            return error
        req._submit(ip=_client_ip(), ua=_user_agent())
        return {"ok": True}
