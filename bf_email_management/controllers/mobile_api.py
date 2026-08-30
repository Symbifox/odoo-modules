"""Mobile API for the email module — consumed by the Odoo Inbox Android app.

A plain REST/JSON contract under ``/bf_email_management/mobile/v1/``, not
Odoo's ``call_kw`` JSON-RPC: the app is a third party, and pinning it to the
ORM's wire format would make every model rename a breaking client change.

Authentication is bearer-token, and the token can only be obtained by
completing a real Odoo web login:

  1. ``GET  /auth/start``   — opened in a browser tab (``auth="user"``, so
     password / Authentik SSO / TOTP all apply), redirects to the app's deep
     link with a single-use code.
  2. ``POST /auth/exchange`` — swaps the code for the durable bearer token.
  3. Every later call carries ``Authorization: Bearer <token>``.

There is no password route here — see bf_email_mobile_device.py for why.

Each authenticated call runs as the device's user (``request.update_env``),
so the per-owner ir.rules on bf.email apply unchanged; the model layer
additionally refuses rows owned by somebody else, which matters because
``group_email_admin`` can read every mailbox in the ORM.

The app also talks to ``bf_sms_archive``'s API on the same instance. The two
are deliberately independent — separate tokens, separate registrations — and
``GET /ping`` is how the app finds out whether this half is installed at all.
"""
import functools
import json
import logging
import urllib.parse

from werkzeug.utils import redirect as wz_redirect

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.bf_email_mobile import TOO_LARGE, UPLOAD_SINGLE_MAX
from ..models.push_transport import safe_push_endpoint

_logger = logging.getLogger(__name__)

BASE = "/bf_email_management/mobile/v1"
API_VERSION = 1
REDIRECT_SCHEMES_PARAM = "bf_email_management.mobile_redirect_schemes"
DEFAULT_REDIRECT_SCHEMES = "odooinbox://"
# Attachments are streamed straight out of raw_rfc822 / ir.attachment. Past
# this size the phone should not be the download path at all.
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _body(**kw):
    """Parsed request body.

    The app posts JSON. A form-encoded POST is already drained into ``kw`` by
    the dispatcher before the handler runs, leaving the raw stream empty — so
    falling back to ``kw`` turns "wrong Content-Type" into a working request
    instead of a silently empty dict and a confusing "missing_parameters".
    """
    raw = request.httprequest.get_data(as_text=True)
    if not raw:
        return dict(kw)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return dict(kw)
    return parsed if isinstance(parsed, dict) else dict(kw)


def _flag(value):
    return str(value).lower() in ("1", "true", "yes")


def _grouped(source):
    """Le repli en conversations, tel que l'app l'affiche en ce moment.

    Absent par défaut = replié, comme ``/threads`` : un client plus ancien qui
    n'envoie pas le drapeau garde le comportement contre lequel il a été
    écrit. Le booléen JSON ``false`` et la chaîne ``"0"`` disent la même chose,
    l'app n'ayant pas à savoir par quel encodage le paramètre voyage.
    """
    value = source.get("grouped", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("0", "false", "no")


def _allowed_redirect(redirect):
    """True when the redirect target is one of this instance's app schemes.

    Without it ``/auth/start`` is an open redirect that hands a live exchange
    code to whatever URL the caller names.
    """
    schemes = (request.env["ir.config_parameter"].sudo().get_param(
        REDIRECT_SCHEMES_PARAM) or DEFAULT_REDIRECT_SCHEMES)
    allowed = tuple(s.strip() for s in schemes.split(",") if s.strip())
    return bool(redirect) and bool(allowed) and redirect.startswith(allowed)


def _authed(fn):
    """Resolve the bearer token, switch the env to its user, or 401."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kw):
        header = request.httprequest.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = request.env["bf.email.mobile.device"]._resolve(token)
        if not device:
            return _json({"error": "unauthorized"}, 401)
        device.sudo().write({"last_seen": fields.Datetime.now()})
        request.update_env(user=device.user_id.id)
        try:
            return fn(self, device, *args, **kw)
        except (UserError, AccessError) as exc:
            return _json({"error": str(exc)}, 400)
        except (TypeError, ValueError) as exc:
            # Almost always a malformed parameter (a string where an int was
            # expected, a bad thread key). That is the caller's mistake, so it
            # gets a 400 — a 500 would tell the app to retry forever.
            _logger.info("Mobile mail API: bad request — %s", exc)
            return _json({"error": "bad_request"}, 400)
        except Exception:  # noqa: BLE001
            _logger.exception("Mobile mail API: unexpected error")
            return _json({"error": "server_error"}, 500)
    return wrapper


class BfEmailMobileApi(http.Controller):

    # ── Discovery ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Capability probe. The app hits this on both module APIs to decide
        which tabs to show, before anyone has logged in."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_email_management")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_email_management",
            "api": API_VERSION,
            "version": module.installed_version or "",
            # Public on purpose: the app themes its instance and login screens
            # before anyone has signed in. Colours and a company name are not
            # a disclosure — the domain already says whose server this is.
            "branding": request.env["bf.email"].sudo()._mobile_branding(),
        })

    # ── Auth (web-login capture) ──────────────────────────────────────
    @http.route(f"{BASE}/auth/start", type="http", auth="user", methods=["GET"],
                csrf=False)
    def auth_start(self, **kw):
        """Issue a single-use code and bounce back into the app.

        Failures come back through the deep link as ``?error=…`` rather than
        as an HTML page. The app chains this against both module APIs in one
        browser session; a dead-end error page on the first leg would strand
        the whole login instead of just disabling one tab.
        """
        redirect = kw.get("redirect") or ""
        state = kw.get("state") or ""
        if not _allowed_redirect(redirect):
            return request.make_response(
                "Redirection non autorisée.", status=400,
                headers=[("Content-Type", "text/plain; charset=utf-8")])

        sep = "&" if "?" in redirect else "?"

        def bounce(**params):
            query = urllib.parse.urlencode({**params, "state": state})
            return wz_redirect(f"{redirect}{sep}{query}", code=302)

        user = request.env.user
        # No dedicated group on this module: bf.email is owner-scoped and open
        # to every internal user. What actually decides whether the app is
        # useful is owning a mailbox.
        if not user.has_group("base.group_user"):
            return bounce(error="no_access")
        has_account = request.env["bf.email.account"].sudo().search_count([
            ("user_id", "=", user.id), ("active", "=", True)])
        if not has_account:
            return bounce(error="no_mailbox")

        code = request.env["bf.email.mobile.device"]._issue_pending(
            user.id, name=kw.get("device_name"))
        return bounce(code=code)

    @http.route(f"{BASE}/auth/exchange", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def auth_exchange(self, **kw):
        data = _body(**kw)
        device = request.env["bf.email.mobile.device"]._exchange(
            (data.get("code") or "").strip())
        if not device:
            return _json({"error": "invalid_or_expired_code"}, 401)
        request.update_env(user=device.user_id.id)
        return _json({
            "token": device.sudo().device_token,
            "user_id": device.user_id.id,
            "config": request.env["bf.email"].get_mobile_config(),
        })

    @http.route(f"{BASE}/logout", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def logout(self, device, **kw):
        device.sudo().write({"active": False, "push_endpoint": False})
        return _json({"ok": True})

    # ── Bootstrap ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/config", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def config(self, device, **kw):
        return _json(request.env["bf.email"].get_mobile_config())

    # ── Reading ───────────────────────────────────────────────────────
    @http.route(f"{BASE}/threads", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def threads(self, device, **kw):
        return _json(request.env["bf.email"].get_mobile_threads(
            filter_name=kw.get("filter") or "inbox",
            search=kw.get("search") or None,
            account_id=int(kw["account_id"]) if kw.get("account_id") else None,
            offset=int(kw.get("offset") or 0),
            limit=int(kw.get("limit") or 25),
            # Default on: folding is what makes this a mail app rather than a
            # message log, so an old client that never sends the flag keeps
            # the behaviour it was written against.
            grouped=kw.get("grouped", "1") not in ("0", "false", "False"),
        ))

    @http.route(f"{BASE}/conversation", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def conversation(self, device, **kw):
        if not kw.get("thread_key"):
            return _json({"error": "missing_thread_key"}, 400)
        return _json(request.env["bf.email"].get_mobile_conversation(
            kw["thread_key"], load_images=_flag(kw.get("load_images"))))

    @http.route(f"{BASE}/message", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def message(self, device, **kw):
        if not kw.get("id"):
            return _json({"error": "missing_id"}, 400)
        return _json(request.env["bf.email"].get_mobile_message(
            int(kw["id"]), load_images=_flag(kw.get("load_images"))))

    @http.route(f"{BASE}/attachment", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    @_authed
    def attachment(self, device, **kw):
        """Stream one attachment.

        Indexed by position in the message's own attachment list, never by
        ir.attachment id: the id space is global, and a device that could name
        an arbitrary one would be reading the whole filestore.
        """
        if not kw.get("email_id") or kw.get("idx") is None:
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(kw["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        found = record._mobile_attachment_bytes(
            int(kw["idx"]), max_bytes=ATTACHMENT_MAX_BYTES)
        if found is TOO_LARGE:
            return _json({"error": "too_large"}, 413)
        if not found:
            return _json({"error": "not_found"}, 404)
        name, mimetype, payload = found
        return request.make_response(payload, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", str(len(payload))),
            ("Content-Disposition",
             "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name)),
            # Attachment bytes must not sit in a shared proxy cache.
            ("Cache-Control", "private, max-age=0, no-store"),
        ])

    # ── Compteurs ─────────────────────────────────────────────────────
    @http.route(f"{BASE}/counts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def counts(self, device, **kw):
        """Les seuls totaux, relus à part de la liste.

        ⚠️ Cette route existe parce que l'app n'avait AUCUN moyen de rafraîchir
        ses pastilles : elles ne descendaient qu'à l'ouverture de l'écran et
        dans la réponse d'une mutation faite depuis le téléphone. Un courriel
        qui arrivait, un ménage fait au navigateur, ou simplement OUVRIR un fil
        — qui marque lu côté serveur — laissaient « Non lus · 5 » au-dessus
        d'une liste qui n'avait plus rien à lire. Tirer pour rafraîchir n'y
        changeait rien. Répondre les totaux sans la page de courriels rend le
        rafraîchissement assez léger pour être fait à chaque relecture.
        """
        return _json({"counts": request.env["bf.email"]._mobile_counts(
            grouped=_grouped(kw))})

    # ── Triage ────────────────────────────────────────────────────────
    @http.route(f"{BASE}/mark_read", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def mark_read(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_mark_read(
            data.get("email_ids"), grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/handle", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def handle(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_set_handled(
            data.get("email_ids"), handled=bool(data.get("handled", True)),
            grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/snooze", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def snooze(self, device, **kw):
        data = _body(**kw)
        counts = request.env["bf.email"].mobile_snooze(
            data.get("email_ids"), data.get("until_ms"),
            grouped=_grouped(data))
        return _json({"ok": True, "counts": counts})

    @http.route(f"{BASE}/attachment/upload", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def attachment_upload(self, device, **kw):
        """Stage one outbound file, returning the id a send can reference.

        Multipart rather than base64-in-JSON: phone attachments are photos and
        PDFs, and base64 would inflate every one of them by a third over a
        mobile link.

        The returned id is only usable by this device, for this user, once —
        see ``_mobile_claim_uploads``.
        """
        upload = request.httprequest.files.get("file")
        if upload is None:
            return _json({"error": "missing_file"}, 400)
        content = upload.read(UPLOAD_SINGLE_MAX + 1)
        if not content:
            return _json({"error": "empty_file"}, 400)
        if len(content) > UPLOAD_SINGLE_MAX:
            return _json({"error": "too_large"}, 413)
        return _json(request.env["bf.email"].mobile_stage_upload(
            device,
            filename=upload.filename,
            content=content,
            mimetype=upload.mimetype,
        ))

    # ── Sending ───────────────────────────────────────────────────────
    @http.route(f"{BASE}/reply", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def reply(self, device, **kw):
        data = _body(**kw)
        if not data.get("email_id"):
            return _json({"error": "missing_email_id"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_reply(
            mode=data.get("mode") or "reply",
            body=data.get("body") or "",
            to=data.get("to"),
            cc=data.get("cc"),
            device=device,
            attachment_ids=data.get("attachment_ids"),
            client_token=data.get("client_token"),
            body_is_html=bool(data.get("body_is_html")),
        ))

    @http.route(f"{BASE}/compose", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def compose(self, device, **kw):
        data = _body(**kw)
        return _json(request.env["bf.email"].mobile_compose(
            to=data.get("to"),
            subject=data.get("subject"),
            body=data.get("body") or "",
            cc=data.get("cc"),
            res_model=data.get("res_model"),
            res_id=data.get("res_id"),
            device=device,
            attachment_ids=data.get("attachment_ids"),
            client_token=data.get("client_token"),
            body_is_html=bool(data.get("body_is_html")),
        ))

    # ── Odoo-side actions ─────────────────────────────────────────────
    @http.route(f"{BASE}/contacts", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def contacts(self, device, **kw):
        """Address-book completion for the composer's To/Cc fields."""
        return _json(request.env["bf.email"].mobile_search_contacts(
            kw.get("q") or "", limit=int(kw.get("limit") or 20)))

    @http.route(f"{BASE}/records", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    @_authed
    def records(self, device, **kw):
        return _json(request.env["bf.email"].mobile_search_records(
            kw.get("model") or "", kw.get("q") or "",
            limit=int(kw.get("limit") or 20)))

    @http.route(f"{BASE}/route", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def route(self, device, **kw):
        data = _body(**kw)
        if not (data.get("email_id") and data.get("res_model")
                and data.get("res_id")):
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_route(data["res_model"], int(data["res_id"])))

    @http.route(f"{BASE}/spawn", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    @_authed
    def spawn(self, device, **kw):
        data = _body(**kw)
        if not (data.get("email_id") and data.get("kind")):
            return _json({"error": "missing_parameters"}, 400)
        record = request.env["bf.email"].browse(int(data["email_id"])).exists()
        if not record or record.user_id.id != request.env.uid:
            return _json({"error": "not_found"}, 404)
        return _json(record.mobile_spawn(data["kind"]))

    # ── Push ──────────────────────────────────────────────────────────
    @http.route(f"{BASE}/register_push", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    @_authed
    def register_push(self, device, **kw):
        """Store the device's UnifiedPush endpoint.

        The server POSTs to this URL on every inbound message, so an endpoint
        resolving to a private address would make the cron a blind-SSRF sink.
        """
        data = _body(**kw)
        endpoint = (data.get("endpoint") or "").strip()
        if not safe_push_endpoint(endpoint):
            return _json({"error": "invalid_endpoint"}, 400)
        device.sudo().write({
            "push_endpoint": endpoint,
            "app_version": data.get("app_version") or device.app_version,
        })
        return _json({"ok": True})
