"""GenFox mobile API — the same conversations and the same tools as the web panel.

Three choices shape this file.

**The same `/chat-stream` as the desktop.** Same tools (writes included), same
`claude_session_id`, so a conversation started on the phone carries on at the
desk and the other way round.

**Asynchronous, streaming notwithstanding.** An agentic turn sometimes runs for
minutes and workers are a scarce resource: holding one SSE connection open per
phone would starve them, and a mobile link drops anyway. So the worker thread
consumes the bridge stream server side and **writes progress into the message**;
the phone polls. Progress is real (text growing, tools appearing) and the turn
outlives the screen that started it.

**The app's device token.** GenFox is a CAPABILITY of the existing mobile
session, not a third account. The module depends on neither half of the app, so
the controller recognises whichever one is installed.
"""

import json
import logging
import threading
import time

import odoo
from odoo import http
from odoo.http import request
from odoo.modules.registry import Registry

from .main import (
    _attach_steering, _check_rate_limit, _generate_smart_title,
    _iter_bridge_stream, _get_settings, usage_vals,
)

_logger = logging.getLogger(__name__)

BASE = "/bf_claude_chat/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

# How often progress is written. One write per token would drown Postgres; one
# write per second would not read as "typing". 400 ms holds both ends.
_FLUSH_SECONDS = 0.4

# Past this, the turn counts as lost rather than left "in progress" forever.
_TURN_TIMEOUT = 900


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _body():
    try:
        return json.loads(request.httprequest.get_data(as_text=True) or "{}")
    except (ValueError, TypeError):
        return {}


def _device():
    header = request.httprequest.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else None
    if not token:
        return None
    for model in _DEVICE_MODELS:
        if model not in request.env:
            continue
        device = request.env[model].sudo()._resolve(token)
        # `_resolve` refuses a revoked DEVICE, nobody was checking the USER: an
        # archived employee whose phone still holds a token would otherwise keep
        # a write-capable assistant acting in their name.
        if device and device.user_id.active:
            return device
    return None


def _tools(tool_log):
    """Tool log -> list, tolerant of an empty or damaged field."""
    if not tool_log:
        return []
    try:
        rows = json.loads(tool_log)
    except (ValueError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _push(env, user, session, text):
    """Tell the device the answer has landed. Never raises."""
    model = "sms.archive.unifiedpush"
    if model not in env:
        return
    try:
        env[model].sudo()._send(user, {
            "type": "genfox",
            "title": session.name or "GenFox",
            "body": text[:180],
            "session_id": session.id,
        })
    except Exception:  # noqa: BLE001
        _logger.warning("GenFox mobile: push failed", exc_info=True)


class _Progress:
    """Accumulates the stream and writes it to the message, without hammering
    the database."""

    def __init__(self, db_name, uid, message_id):
        self.db_name = db_name
        self.uid = uid
        self.message_id = message_id
        self.text = ""
        self.tools = []
        self.last_write = 0.0

    def text_received(self, delta):
        self.text += delta or ""
        self.write()

    def tool_received(self, name):
        self.tools.append({"name": name, "at": len(self.text)})
        self.write(force=True)  # a tool starting deserves to be seen at once

    def write(self, force=False, **extra):
        now = time.monotonic()
        if not force and now - self.last_write < _FLUSH_SECONDS:
            return
        self.last_write = now
        vals = {"tool_log": json.dumps(self.tools)}
        if self.text:
            vals["content"] = self.text
        vals.update(extra)
        try:
            registry = Registry(self.db_name)
            with registry.cursor() as cr:
                env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
                message = env["claude.chat.message"].browse(self.message_id)
                if message.exists():
                    message.write(vals)
        except Exception:  # noqa: BLE001
            # Progress is a comfort: failing to write it must not break the turn.
            _logger.warning("GenFox mobile: progress write failed", exc_info=True)


def _run_turn(db_name, uid, session_id, message_id, question, payload,
              socket_path, timeout, api_key, session_was_new):
    """Worker thread: consume the bridge stream and persist as it flows."""
    progress = _Progress(db_name, uid, message_id)
    final, current_event, buffer = {}, None, b""
    started = time.monotonic()

    try:
        for chunk in _iter_bridge_stream(socket_path, "/chat-stream", payload, timeout):
            if time.monotonic() - started > _TURN_TIMEOUT:
                raise TimeoutError("turn ran too long")
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.strip()
                if line.startswith(b"event:"):
                    current_event = line[6:].strip()
                elif line.startswith(b"data:"):
                    try:
                        data = json.loads(line[5:])
                    except Exception:  # noqa: BLE001
                        continue
                    if current_event == b"text":
                        progress.text_received(data.get("delta"))
                    elif current_event == b"tool":
                        progress.tool_received(data.get("name") or "tool")
                    elif current_event in (b"done", b"error"):
                        final = data
                        final["_event"] = current_event.decode()
    except Exception as exc:  # noqa: BLE001
        _logger.exception("GenFox mobile: stream interrupted")
        final = final or {
            "response": progress.text or "The assistant was interrupted (%s)." % (
                type(exc).__name__),
            "_event": "error", "reason": "stream",
        }

    answer = (final.get("response") or progress.text or "").strip()
    failed = final.get("_event") == "error" or not answer

    try:
        registry = Registry(db_name)
        with registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            message = env["claude.chat.message"].browse(message_id)
            if not message.exists():
                return
            message.write({
                "content": answer or "The assistant returned nothing.",
                "state": "error" if failed else "done",
                "tool_log": json.dumps(progress.tools),
                **usage_vals(final),
            })
            session = message.session_id
            vals = {}
            new_sid = final.get("session_id")
            if new_sid and new_sid != session.claude_session_id:
                vals["claude_session_id"] = new_sid
            # Same anti-poison as the web panel: a thread that fails repeatedly
            # is forked on the next turn rather than resumed.
            if failed:
                vals["stream_fail_count"] = (session.stream_fail_count or 0) + 1
                vals["last_stream_error"] = (final.get("reason") or "error")[:64]
            elif session.stream_fail_count:
                vals["stream_fail_count"] = 0
                vals["last_stream_error"] = False
            if session.name in ("New Chat", False):
                vals["name"] = (question[:57] + "...") if len(question) > 60 else question
            if vals:
                session.write(vals)
            if not failed:
                _push(env, env["res.users"].browse(uid), session, answer)
    except Exception:  # noqa: BLE001
        _logger.exception("GenFox mobile: writing the answer failed")
        return

    # Smart title, as on the desktop, on a brand-new conversation.
    if session_was_new and not failed:
        fallback = question[:60] + ("..." if len(question) > 60 else "")
        threading.Thread(
            target=_generate_smart_title,
            args=(db_name, session_id, fallback, question, answer, api_key, socket_path),
            daemon=True,
        ).start()


class BfClaudeChatMobileApi(http.Controller):

    # -- Discovery ----------------------------------------------------
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        settings = _get_settings()
        payload = {
            "ok": True,
            "module": "bf_claude_chat",
            "api": 2,
            "enabled": bool(settings["enabled"]),
            # Full parity since api 2: the same tools as the desktop.
            "readonly": False,
        }
        # The app pings before it holds a token (it even strips the header on
        # this route), so the endpoint stays public. But an anonymous caller has
        # no business learning which build is installed: the version only goes
        # to a recognised device.
        if _device():
            module = request.env["ir.module.module"].sudo().search(
                [("name", "=", "bf_claude_chat")], limit=1)
            payload["version"] = module.installed_version or ""
        return _json(payload)

    # -- Conversations ------------------------------------------------
    @http.route(f"{BASE}/sessions", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def sessions(self, **kw):
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        # No filter on origin any more: the conversations are the same on both
        # sides, which is the whole point of parity.
        rows = request.env["claude.chat.session"].search_read(
            [("user_id", "=", request.env.user.id)],
            ["name", "write_date", "message_count", "origin"],
            order="write_date desc", limit=30,
        )
        return _json({"sessions": rows})

    @http.route(f"{BASE}/messages", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def messages(self, **kw):
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        session = request.env["claude.chat.session"].browse(
            int(kw.get("session_id") or 0))
        if not session.exists() or session.user_id != request.env.user:
            return _json({"error": "conversation not found"}, 404)
        rows = request.env["claude.chat.message"].search_read(
            [("session_id", "=", session.id), ("internal", "=", False)],
            ["role", "content", "state", "tool_log", "create_date",
             "input_tokens", "output_tokens", "total_tokens", "cost_usd",
             "duration_ms"],
            order="create_date asc, id asc", limit=200,
        )
        for row in rows:
            row["tools"] = _tools(row.pop("tool_log", None))
        return _json({"session_id": session.id, "session_name": session.name,
                      "messages": rows})

    # -- Asking a question --------------------------------------------
    @http.route(f"{BASE}/ask", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def ask(self, **kw):
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        user = request.env.user

        settings = _get_settings()
        if not settings["enabled"]:
            return _json({"error": "The assistant is not enabled here."}, 400)
        if not _check_rate_limit(user.id):
            return _json({"error": "Too many requests - try again shortly."}, 429)

        body = _body()
        question = (body.get("message") or "").strip()
        if not question:
            return _json({"error": "Empty question."}, 400)
        if len(question) > 4000:
            return _json({"error": "Question too long."}, 400)

        Session = request.env["claude.chat.session"]
        session_was_new = not body.get("session_id")
        if body.get("session_id"):
            session = Session.browse(int(body["session_id"]))
            if not session.exists() or session.user_id != user:
                return _json({"error": "conversation not found"}, 404)
        else:
            session = Session.create({
                "name": "New Chat", "user_id": user.id, "origin": "mobile",
            })

        Message = request.env["claude.chat.message"]
        Message.create({
            "session_id": session.id, "role": "user", "content": question,
        })
        pending = Message.create({
            # `content` is required: a placeholder, replaced as the stream flows.
            "session_id": session.id, "role": "assistant", "content": "...",
            "state": "pending",
        })

        # Same anti-poison as the web panel: a thread that failed repeatedly
        # starts over rather than being resumed.
        claude_sid = session.claude_session_id or None
        if claude_sid and session.stream_fail_count >= 3:
            claude_sid = None

        payload = {
            "session_id": claude_sid,
            "message": question,
            "user_name": user.name,
            "user_id": user.id,
            "user_email": user.email or "",
            "model": settings["model"],
            "max_turns": settings["max_turns"],
            "tenant": settings["tenant"],
        }
        if settings["api_key"]:
            payload["api_key"] = settings["api_key"]
        _attach_steering(request.env, payload, None)

        # The thread must start AFTER the write, otherwise it looks for a
        # message nobody can see yet.
        request.env.cr.commit()
        threading.Thread(
            target=_run_turn,
            args=(request.env.cr.dbname, user.id, session.id, pending.id, question,
                  payload, settings["socket"], settings["timeout"],
                  settings.get("api_key", ""), session_was_new),
            daemon=True,
        ).start()

        return _json({
            "ok": True,
            "session_id": session.id,
            "turn_id": pending.id,
            "state": "pending",
        })

    @http.route(f"{BASE}/turn", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def turn(self, **kw):
        """Where a turn stands - PARTIAL text included, which is what gives the
        phone progressive writing without holding a connection open."""
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        message = request.env["claude.chat.message"].browse(int(kw.get("turn_id") or 0))
        if not message.exists() or message.session_id.user_id != request.env.user:
            return _json({"error": "turn not found"}, 404)
        text = message.content or ""
        return _json({
            "turn_id": message.id,
            "session_id": message.session_id.id,
            "session_name": message.session_id.name,
            "state": message.state,
            # The initial placeholder is not an answer: do not show it.
            "text": "" if text == "..." else text,
            "tools": _tools(message.tool_log),
            "usage": {
                "input_tokens": message.input_tokens,
                "output_tokens": message.output_tokens,
                "total_tokens": message.total_tokens,
                "cost_usd": message.cost_usd,
                "duration_ms": message.duration_ms,
            },
        })

    @http.route(f"{BASE}/delete-session", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def delete_session(self, **kw):
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        session = request.env["claude.chat.session"].browse(
            int(_body().get("session_id") or 0))
        if not session.exists() or session.user_id != request.env.user:
            return _json({"error": "conversation not found"}, 404)
        session.write({"active": False})
        return _json({"ok": True})
