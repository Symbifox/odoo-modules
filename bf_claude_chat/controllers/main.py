import html as _html
import json
import logging
import re as _re
import socket
import threading
import time
from collections import defaultdict

import odoo
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Per-user rate limiting for /chat endpoint
_rate_limit_lock = threading.Lock()
_rate_limit_data = defaultdict(list)  # user_id -> [timestamps]
_RATE_LIMIT_MAX = 30  # max requests
_RATE_LIMIT_WINDOW = 60  # per 60 seconds


def _check_rate_limit(user_id):
    """Return True if the user is within rate limits, False if exceeded."""
    now = time.monotonic()
    with _rate_limit_lock:
        timestamps = _rate_limit_data[user_id]
        # Prune old entries
        cutoff = now - _RATE_LIMIT_WINDOW
        _rate_limit_data[user_id] = [t for t in timestamps if t > cutoff]
        if len(_rate_limit_data[user_id]) >= _RATE_LIMIT_MAX:
            return False
        _rate_limit_data[user_id].append(now)
        return True


def _sanitize_html(html_str):
    """Sanitize HTML: strip dangerous tags/attributes, block javascript: URLs."""
    from odoo.tools import html_sanitize
    return html_sanitize(html_str, sanitize_attributes=True, strip_classes=False)


def _markdown_to_html(md):
    """Convert markdown to HTML (Python port of JS markdownToHtml)."""
    if not md:
        return ""

    text = md

    # If content already has block-level HTML tags, sanitize and return
    if _re.search(r"<(?:p|h[1-6]|ul|ol|table|div|hr)\b", text.strip(), _re.I):
        return _sanitize_html(text.strip())

    # Fenced code blocks — extract and replace with placeholders
    code_blocks = []

    def _save_code_block(m):
        lang = m.group(1) or ""
        code = m.group(2)
        code = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        code_blocks.append(
            f'<pre><code class="language-{lang}">{code}</code></pre>'
        )
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = _re.sub(r"```(\w*)\n([\s\S]*?)```", _save_code_block, text)

    # Inline code
    text = _re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Headers (largest to smallest so ### doesn't match before #####)
    text = _re.sub(r"^##### (.+)$", r"<h5>\1</h5>", text, flags=_re.M)
    text = _re.sub(r"^#### (.+)$", r"<h4>\1</h4>", text, flags=_re.M)
    text = _re.sub(r"^### (.+)$", r"<h4>\1</h4>", text, flags=_re.M)
    text = _re.sub(r"^## (.+)$", r"<h3>\1</h3>", text, flags=_re.M)
    text = _re.sub(r"^# (.+)$", r"<h3>\1</h3>", text, flags=_re.M)

    # Bold + italic
    text = _re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    text = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = _re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)

    # Links — validate href using URL scheme whitelist
    def _safe_link(m):
        from urllib.parse import urlparse
        label, href = m.group(1), m.group(2)
        # Strip whitespace and control chars that could bypass scheme detection
        clean_href = _re.sub(r'[\s\x00-\x1f]', '', href)
        try:
            parsed = urlparse(clean_href)
            scheme = (parsed.scheme or '').lower()
            # Allow only safe schemes; relative URLs have no scheme
            if scheme and scheme not in ('http', 'https', 'mailto', 'tel'):
                return _html.escape(label)
        except Exception:
            return _html.escape(label)
        return f'<a href="{_html.escape(clean_href)}" target="_blank">{label}</a>'

    text = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _safe_link, text)

    # Markdown tables
    def _convert_table(m):
        table_block = m.group(0).strip()
        rows = [r for r in table_block.split("\n") if r.strip()]
        if len(rows) < 2:
            return table_block
        data_rows = [r for r in rows if not _re.match(r"^\|[\s\-:|]+\|$", r)]
        if not data_rows:
            return table_block
        out = '<table style="border-collapse:collapse;margin:4px 0">'
        for i, row in enumerate(data_rows):
            cells = row.split("|")
            # Remove first and last empty splits from leading/trailing |
            cells = cells[1:-1] if len(cells) > 2 else cells
            tag = "th" if i == 0 else "td"
            style = "border:1px solid #ddd;padding:4px 8px"
            out += "<tr>" + "".join(
                f"<{tag} style=\"{style}\">{c.strip()}</{tag}>" for c in cells
            ) + "</tr>"
        out += "</table>"
        return out

    text = _re.sub(r"((?:^\|.+\|$\n?)+)", _convert_table, text, flags=_re.M)

    # Horizontal rules
    text = _re.sub(r"^---+$", "<hr/>", text, flags=_re.M)

    # Ordered lists (1. item) — use a distinct marker to avoid collision
    text = _re.sub(r"^\d+\. (.+)$", r"<oli>\1</oli>", text, flags=_re.M)
    text = _re.sub(r"((?:<oli>.*?</oli>\n?)+)", lambda m: "<ol>" + m.group(1).replace("<oli>", "<li>").replace("</oli>", "</li>") + "</ol>", text)

    # Unordered lists
    text = _re.sub(r"^[-*] (.+)$", r"<li>\1</li>", text, flags=_re.M)
    text = _re.sub(r"((?:<li>.*?</li>\n?)+)", r"<ul>\1</ul>", text)

    # Paragraphs: split by double newline
    parts = _re.split(r"\n{2,}", text)
    processed = []
    for block in parts:
        block = block.strip()
        if not block:
            continue
        if _re.match(r"^<(?:h[1-6]|ul|ol|pre|table|div|blockquote|hr)", block):
            processed.append(block)
        else:
            processed.append(f'<p>{block.replace(chr(10), "<br/>")}</p>')
    text = "\n".join(processed)

    # Restore code blocks
    for i, cb in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{i}\x00", cb)

    return _sanitize_html(text)


# Defaults (overridden by Settings)
_DEFAULT_SOCKET = "/run/claude-bridge/bridge.sock"
_DEFAULT_TIMEOUT = 660


def _get_settings():
    """Read GenFox settings from ir.config_parameter."""
    ICP = request.env["ir.config_parameter"].sudo()
    # Decrypt API key via the settings model helper
    encrypted_key = ICP.get_param("bf_claude_chat.api_key_encrypted", "")
    from odoo.addons.bf_claude_chat.models.res_config_settings import (
        ResConfigSettings,
    )
    api_key = ResConfigSettings._decrypt_api_key(request.env, encrypted_key)
    return {
        "enabled": ICP.get_param("bf_claude_chat.enabled", "True") == "True",
        "model": ICP.get_param("bf_claude_chat.model", "sonnet"),
        "max_turns": int(ICP.get_param("bf_claude_chat.max_turns", "25")),
        "api_key": api_key,
        "tenant": ICP.get_param("bf_claude_chat.tenant", "pme"),
        "socket": ICP.get_param("bf_claude_chat.bridge_socket", _DEFAULT_SOCKET),
        "timeout": int(ICP.get_param("bf_claude_chat.timeout", str(_DEFAULT_TIMEOUT))),
    }


def _call_bridge(endpoint, payload, socket_path, timeout):
    """Call the bridge service via Unix socket."""
    body = json.dumps(payload).encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        req = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        sock.sendall(req)

        # Read response
        chunks = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode()

        # Parse HTTP response — split headers from body
        header_end = raw.find("\r\n\r\n")
        if header_end == -1:
            raise ValueError("Malformed HTTP response")
        status_line = raw[:raw.find("\r\n")]
        status_code = int(status_line.split(" ", 2)[1])
        resp_body = raw[header_end + 4:]

        # Handle chunked transfer encoding
        headers_block = raw[:header_end].lower()
        if "transfer-encoding: chunked" in headers_block:
            decoded = []
            pos = 0
            while pos < len(resp_body):
                nl = resp_body.find("\r\n", pos)
                if nl == -1:
                    break
                chunk_size = int(resp_body[pos:nl], 16)
                if chunk_size == 0:
                    break
                decoded.append(resp_body[nl + 2:nl + 2 + chunk_size])
                pos = nl + 2 + chunk_size + 2
            resp_body = "".join(decoded)

        if status_code >= 400:
            raise ValueError(f"Bridge returned HTTP {status_code}: {resp_body[:200]}")
        return json.loads(resp_body)
    finally:
        sock.close()


def _generate_smart_title(db_name, session_id, fallback, user_msg, asst_resp, api_key, socket_path):
    """Background thread: call /generate-title and update session name."""
    try:
        payload = {
            "user_message": user_msg[:300],
            "assistant_response": asst_resp[:300],
        }
        if api_key:
            payload["api_key"] = api_key
        data = _call_bridge("/generate-title", payload, socket_path, 20)
        title = data.get("title", "").strip()
        if not title:
            return
        # Open a new DB cursor (standard Odoo pattern for async writes)
        registry = odoo.registry(db_name)
        with registry.cursor() as cr:
            env = odoo.api.Environment(cr, odoo.SUPERUSER_ID, {})
            session = env["claude.chat.session"].browse(session_id)
            if session.exists() and session.name == fallback:
                session.write({"name": title})
    except Exception:
        _logger.warning("Smart title generation failed", exc_info=True)


def _validated_context_ref(env, context):
    """(model, res_id) from the client-supplied page context, or (None, None).

    Single choke-point for the access check. It used to live only in the
    "new session" branch of send_message, while the block that actually ships
    the value to the bridge re-derived it from the raw payload and gated on
    nothing but ``model in env`` — so every message on an EXISTING session
    forwarded whatever model/res_id the caller typed. The bridge resolves that
    reference with its own credentials, so an unchecked pair is a way to have
    records summarised that the caller cannot read.

    Enforces the CALLER's access (ACL + record rules + multi-company) before the
    reference is trusted anywhere. Returns (None, None) on anything unusable.
    """
    if not context or not isinstance(context, dict):
        return None, None
    model = str(context.get("model", ""))[:64]
    try:
        res_id = int(context["res_id"]) if context.get("res_id") else 0
    except (TypeError, ValueError):
        return None, None
    if not model or not res_id or model not in env:
        return None, None
    try:
        record = env[model].browse(res_id)
        if not record.exists():
            return None, None
        record.check_access("read")
    except Exception:
        _logger.debug(
            "claude chat: refused unvalidated context %s/%s", model, res_id,
        )
        return None, None
    return model, res_id


def _resolve_persona_summary(env, model, res_id):
    """Look up the persona summary for a record, if bf_persona is installed.

    Resolves the partner from the record (directly when model is res.partner,
    via the standard partner_id field otherwise), then returns the persona's
    claude_context_summary. Silent on any failure so chat stays responsive.
    """
    if not model or not res_id or "contact.persona" not in env or model not in env:
        return ""
    try:
        # Enforce the CALLER's read access on the record before any sudo, so a
        # crafted context (arbitrary model/res_id) cannot surface a record the
        # user is not allowed to see (record rules + multi-company). Callers now
        # arrive pre-validated via _validated_context_ref; this stays as the
        # second lock on the sudo() below.
        record = env[model].browse(res_id)
        if not record.exists():
            return ""
        record.check_access("read")
        partner_id = None
        if model == "res.partner":
            partner_id = res_id
        else:
            partner_field = record._fields.get("partner_id")
            if partner_field and partner_field.type == "many2one" \
                    and partner_field.comodel_name == "res.partner":
                partner_id = record.sudo().partner_id.id
        if not partner_id:
            return ""
        persona = env["contact.persona"].sudo().search(
            [("partner_id", "=", partner_id)], limit=1,
        )
        return persona.claude_context_summary or ""
    except Exception:
        _logger.debug("Persona lookup failed for %s/%s", model, res_id, exc_info=True)
        return ""


class ClaudeChatController(http.Controller):

    @http.route("/claude-chat/send", type="json", auth="user", methods=["POST"])
    def send_message(self, session_id=None, message="", context=None):
        """Send a message to GenFox via the bridge service."""
        settings = _get_settings()

        if not settings["enabled"]:
            return {"error": "GenFox is disabled. An administrator can enable it in Settings."}

        if not message.strip():
            return {"error": "Empty message"}

        user = request.env.user

        # Rate limiting: max 30 requests per minute per user
        if not _check_rate_limit(user.id):
            return {"error": "Trop de requetes. Veuillez patienter avant de reessayer."}
        Session = request.env["claude.chat.session"]
        Message = request.env["claude.chat.message"]

        # Resolve the page context ONCE, access-checked, for both the session
        # record and the bridge payload below.
        ctx_model, ctx_res_id = _validated_context_ref(request.env, context)

        # Find or create Odoo session
        if session_id:
            session = Session.browse(int(session_id))
            if not session.exists() or session.user_id != user:
                return {"error": "Session not found"}
        else:
            vals = {"name": "New Chat", "user_id": user.id}
            if ctx_model and ctx_res_id:
                vals["res_model"] = ctx_model
                vals["res_id"] = ctx_res_id
            session = Session.create(vals)

        # Save user message
        Message.create({
            "session_id": session.id,
            "role": "user",
            "content": message.strip(),
        })

        # Build bridge payload with settings
        bridge_payload = {
            "session_id": session.claude_session_id or None,
            "message": message.strip(),
            "user_name": user.name,
            "user_id": user.id,
            "user_email": user.email or "",
            "model": settings["model"],
            "max_turns": settings["max_turns"],
            "tenant": settings["tenant"],
        }
        if settings["api_key"]:
            bridge_payload["api_key"] = settings["api_key"]

        # Pass Odoo page context if provided. model/res_id travel ONLY when
        # _validated_context_ref cleared them for this caller; the cosmetic
        # fields are always safe to forward.
        if context and isinstance(context, dict):
            bridge_payload["context"] = {
                "display_name": str(context.get("display_name", ""))[:200],
                "view_type": str(context.get("view_type", ""))[:20],
                "url": str(context.get("url", ""))[:500],
            }
            if ctx_model and ctx_res_id:
                bridge_payload["context"]["model"] = ctx_model
                bridge_payload["context"]["res_id"] = ctx_res_id
                persona_summary = _resolve_persona_summary(
                    request.env, ctx_model, ctx_res_id,
                )
                if persona_summary:
                    bridge_payload["context"]["persona_summary"] = persona_summary[:2000]

        # Call bridge service via Unix socket
        try:
            data = _call_bridge(
                "/chat", bridge_payload,
                settings["socket"], settings["timeout"],
            )
        except socket.timeout:
            _logger.error("Bridge service timed out")
            return {"error": "GenFox is taking too long to respond. Please try again."}
        except (ConnectionRefusedError, FileNotFoundError):
            _logger.error("Bridge service unreachable at %s", settings["socket"])
            return {"error": "GenFox is unavailable. Please contact your administrator."}
        except Exception:
            _logger.exception("Bridge service error")
            return {"error": "An unexpected error occurred."}

        # Update session with Claude's session ID
        if data.get("session_id") and data["session_id"] != session.claude_session_id:
            session.write({"claude_session_id": data["session_id"]})

        # Auto-title on first exchange: set fallback immediately, then try smart title
        if session.name == "New Chat" and data.get("response"):
            fallback = message.strip()[:60]
            if len(message.strip()) > 60:
                fallback += "..."
            session.write({"name": fallback})

            # Fire background thread to generate a smart title
            db_name = request.env.cr.dbname
            session_id = session.id
            api_key = settings.get("api_key", "")
            socket_path = settings["socket"]
            user_msg = message.strip()
            asst_resp = data.get("response", "")
            threading.Thread(
                target=_generate_smart_title,
                args=(db_name, session_id, fallback, user_msg, asst_resp, api_key, socket_path),
                daemon=True,
            ).start()

        # Save assistant message
        assistant_msg = Message.create({
            "session_id": session.id,
            "role": "assistant",
            "content": data.get("response", "(No response)"),
        })

        return {
            "session_id": session.id,
            "claude_session_id": data.get("session_id"),
            "response": data.get("response", "(No response)"),
            "message_id": assistant_msg.id,
            "session_name": session.name,
        }

    @http.route("/claude-chat/sessions", type="json", auth="user", methods=["POST"])
    def list_sessions(self, res_model=None, res_id=None):
        """List the current user's chat sessions, optionally filtered by record context."""
        domain = [("user_id", "=", request.env.user.id)]
        if res_model and res_id:
            domain.append(("res_model", "=", str(res_model)[:64]))
            domain.append(("res_id", "=", int(res_id)))
        sessions = request.env["claude.chat.session"].search_read(
            domain,
            ["name", "write_date", "message_count", "res_model", "res_id"],
            order="write_date desc",
            limit=50,
        )
        return {"sessions": sessions}

    @http.route("/claude-chat/messages", type="json", auth="user", methods=["POST"])
    def get_messages(self, session_id):
        """Get all messages for a session."""
        session = request.env["claude.chat.session"].browse(int(session_id))
        if not session.exists() or session.user_id != request.env.user:
            return {"error": "Session not found"}

        messages = request.env["claude.chat.message"].search_read(
            [("session_id", "=", session.id)],
            ["role", "content", "create_date"],
            order="create_date asc, id asc",
        )
        return {"messages": messages, "session_name": session.name}

    @http.route("/claude-chat/rename-session", type="json", auth="user", methods=["POST"])
    def rename_session(self, session_id, name=""):
        """Rename a chat session."""
        session = request.env["claude.chat.session"].browse(int(session_id))
        if not session.exists() or session.user_id != request.env.user:
            return {"error": "Session not found"}

        # Sanitize: strip HTML, limit length
        clean_name = _re.sub(r"<[^>]+>", "", str(name)).strip()[:120]
        if not clean_name:
            return {"error": "Name cannot be empty"}

        session.write({"name": clean_name})
        return {"status": "ok", "name": clean_name}

    @http.route("/claude-chat/delete-session", type="json", auth="user", methods=["POST"])
    def delete_session(self, session_id):
        """Archive a chat session."""
        session = request.env["claude.chat.session"].browse(int(session_id))
        if not session.exists() or session.user_id != request.env.user:
            return {"error": "Session not found"}
        session.write({"active": False})
        return {"status": "ok"}

    @http.route("/claude-chat/search-tasks", type="json", auth="user", methods=["POST"])
    def search_tasks(self, query=""):
        """Search project tasks by name."""
        Task = request.env["project.task"]
        results = Task.name_search(query, limit=10)
        return {"tasks": [{"id": r[0], "name": r[1]} for r in results]}

    @http.route("/claude-chat/share-to-task", type="json", auth="user", methods=["POST"])
    def share_to_task(self, session_id, task_id):
        """Post a conversation to a task's chatter as an internal note."""
        user = request.env.user
        session = request.env["claude.chat.session"].browse(int(session_id))
        if not session.exists() or session.user_id != user:
            return {"error": "Session not found"}

        task = request.env["project.task"].browse(int(task_id))
        if not task.exists():
            return {"error": "Task not found"}

        messages = request.env["claude.chat.message"].search(
            [("session_id", "=", session.id)],
            order="create_date asc, id asc",
        )
        if not messages:
            return {"error": "No messages to share"}

        # Build HTML body — escape user content, sanitize assistant content
        title = _html.escape(_re.sub(r"<[^>]+>", "", session.name or "Chat"))
        body = (
            f'<div style="font-family:sans-serif;max-width:700px">'
            f'<h3 style="margin:0 0 12px">GenFox: {title}</h3>'
        )
        for msg in messages:
            if msg.role == "user":
                content = _html.escape(msg.content or "")
                body += (
                    f'<div style="text-align:right;margin:8px 0">'
                    f'<span style="display:inline-block;background:#714bea;color:white;'
                    f'padding:6px 12px;border-radius:12px;max-width:80%;text-align:left">'
                    f'{content}</span></div>'
                )
            else:
                content = _markdown_to_html(msg.content or "")
                body += (
                    f'<div style="text-align:left;margin:8px 0">'
                    f'<div style="display:inline-block;background:#f4f4f5;color:#333;'
                    f'padding:6px 12px;border-radius:12px;max-width:80%;text-align:left">'
                    f'{content}</div></div>'
                )
        body += "</div>"

        task.message_post(
            body=body,
            subtype_xmlid="mail.mt_note",
            body_is_html=True,
        )
        return {"status": "ok", "task_name": task.name}
