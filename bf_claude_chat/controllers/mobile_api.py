"""API mobile de GenFox — mêmes conversations et mêmes outils que le panneau web.

Trois choix structurent ce fichier.

**Le même `/chat-stream` que le bureau.** Mêmes outils (écriture comprise), même
`claude_session_id`, donc une conversation commencée au téléphone se poursuit à
l'écran et l'inverse. C'était volontairement en lecture seule à la livraison ;
Olivier a tranché pour la parité complète le 2026-08-16.

**Asynchrone, malgré le flux.** Un tour agentique dure parfois des minutes et le
serveur n'a que deux travailleurs : tenir un SSE ouvert par téléphone les
affamerait, et un lien mobile lâche de toute façon. Le fil d'exécution consomme
donc le flux du bridge côté serveur et **écrit l'avancement dans le message** ;
le téléphone sonde. La progression est réelle (texte qui pousse, outils qui
apparaissent) et le tour survit à l'écran qui l'a lancé.
⚠️ Ne jamais tuer le CLI à l'événement `done` : voir `_reap_after_result` côté
bridge — c'est le tour SUIVANT qui perdrait la mémoire.

**Jeton d'appareil de l'app.** GenFox est une CAPACITÉ de la session mobile
existante, pas un troisième compte. Le module ne dépendant d'aucune des deux
moitiés de l'app, le contrôleur reconnaît celle qui est là.
"""

import json
import logging

from odoo import fields, http
from odoo.http import request

from . import turns
from .main import (
    _attach_identity, _attach_steering, _check_rate_limit, _get_settings,
)

_logger = logging.getLogger(__name__)

BASE = "/bf_claude_chat/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

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
        # `_resolve` refuses a revoked APPAREIL, personne ne vérifie l'USAGER :
        # un employé archivé dont le téléphone garde son jeton conserverait
        # sinon un assistant capable d'écrire, en son nom.
        if device and device.user_id.active:
            return device
    return None


def _tools(tool_log):
    """Journal d'outils → liste, tolérante à un champ vide ou abîmé."""
    if not tool_log:
        return []
    try:
        rows = json.loads(tool_log)
    except (ValueError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _push(env, user, session, text):
    """Prévient l'appareil quand la réponse arrive. Ne lève jamais."""
    model = "sms.archive.unifiedpush"
    if model not in env:
        return
    try:
        env[model].sudo()._send(user, {
            "type": "genfox",
            "title": session.name or "Gen",
            "body": text[:180],
            "session_id": session.id,
        })
    except Exception:  # noqa: BLE001
        _logger.warning("GenFox mobile : poussée impossible", exc_info=True)


class BfClaudeChatMobileApi(http.Controller):

    # ── Découverte ────────────────────────────────────────────────────
    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        settings = _get_settings()
        payload = {
            "ok": True,
            "module": "bf_claude_chat",
            "api": 3,
            "enabled": bool(settings["enabled"]),
            # Parité complète depuis l'api 2 : mêmes outils qu'au bureau.
            "readonly": False,
        }
        # L'app sonde AVANT d'avoir un jeton (elle retire même l'en-tête sur
        # cette route) : le point reste donc public. Mais un appelant anonyme
        # n'a pas à apprendre quelle version tourne — la version ne part qu'à
        # un appareil reconnu.
        if _device():
            module = request.env["ir.module.module"].sudo().search(
                [("name", "=", "bf_claude_chat")], limit=1)
            payload["version"] = module.installed_version or ""
        return _json(payload)

    # ── Conversations ─────────────────────────────────────────────────
    @http.route(f"{BASE}/sessions", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def sessions(self, **kw):
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        # Plus de filtre par origine : les conversations sont les mêmes des deux
        # côtés, c'est tout l'objet de la parité.
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
            return _json({"error": "conversation introuvable"}, 404)
        rows = request.env["claude.chat.message"].search_read(
            [("session_id", "=", session.id), ("internal", "=", False)],
            ["role", "content", "state", "tool_log", "create_date",
             "input_tokens", "output_tokens", "cache_read_tokens",
             "cache_write_tokens", "net_tokens", "total_tokens", "cost_usd",
             "duration_ms"],
            order="create_date asc, id asc", limit=200,
        )
        for row in rows:
            row["tools"] = _tools(row.pop("tool_log", None))
        return _json({"session_id": session.id, "session_name": session.name,
                      "messages": rows})

    # ── Poser une question ────────────────────────────────────────────
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
            return _json({"error": "L'assistant n'est pas activé ici."}, 400)
        if not _check_rate_limit(user.id):
            return _json({"error": "Trop de requêtes — réessaie dans un instant."}, 429)

        body = _body()
        question = (body.get("message") or "").strip()
        if not question:
            return _json({"error": "Question vide."}, 400)
        if len(question) > 4000:
            return _json({"error": "Question trop longue."}, 400)

        Session = request.env["claude.chat.session"]
        session_was_new = not body.get("session_id")
        if body.get("session_id"):
            session = Session.browse(int(body["session_id"]))
            if not session.exists() or session.user_id != user:
                return _json({"error": "conversation introuvable"}, 404)
        else:
            session = Session.create({
                "name": "New Chat", "user_id": user.id, "origin": "mobile",
            })

        Message = request.env["claude.chat.message"]
        Message.create({
            "session_id": session.id, "role": "user", "content": question,
        })
        # sudo : les champs du tour ne s'écrivent que côté serveur
        # (`TURN_FIELDS`), et la session vient d'être vérifiée à cet usager.
        pending = Message.sudo().create({
            # `content` est requis : un point d'attente, remplacé au fil du flux.
            "session_id": session.id, "role": "assistant", "content": "…",
            "state": "pending",
        })

        # Même anti-poison que le panneau web : un fil qui a échoué en série
        # repart de zéro plutôt que d'être repris.
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
        _attach_identity(request.env, payload)
        _attach_steering(request.env, payload, None)

        # Le même fil que le bureau (`controllers/turns.py`) : il écrit
        # l'avancement dans le message, survit au processus qui l'a lancé
        # (le cron s'y rattache), et reprend seul une fin propre du pont.
        # La clé d'API n'est pas stockée : le fil la relit au départ.
        reglages = turns.turn_settings(request.env)
        pending.sudo().write({
            "turn_key": turns.new_turn_key(request.env.cr.dbname, pending.id),
            "turn_payload": json.dumps(payload),
            "runner_heartbeat": fields.Datetime.now(),
        })
        # Le fil doit démarrer APRÈS l'écriture, sinon il cherche un message que
        # personne ne voit encore.
        request.env.cr.commit()
        turns.start_runner(
            request.env.cr.dbname, pending.id, settings["socket"], settings["timeout"],
            max_continue=reglages["auto_continue"],
            wall_seconds=reglages["wall_seconds"], session_was_new=session_was_new,
        )

        return _json({
            "ok": True,
            "session_id": session.id,
            "turn_id": pending.id,
            "state": "pending",
        })

    @http.route(f"{BASE}/turn", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def turn(self, **kw):
        """Où en est un tour — texte PARTIEL compris, c'est ce qui donne au
        téléphone l'écriture progressive sans tenir de connexion ouverte."""
        device = _device()
        if not device:
            return _json({"error": "unauthorized"}, 401)
        request.update_env(user=device.user_id.id)
        message = request.env["claude.chat.message"].browse(int(kw.get("turn_id") or 0))
        if not message.exists() or message.session_id.user_id != request.env.user:
            return _json({"error": "tour introuvable"}, 404)
        texte = message.content or ""
        return _json({
            "turn_id": message.id,
            "session_id": message.session_id.id,
            "session_name": message.session_id.name,
            "state": message.state,
            # Le point d'attente initial n'est pas une réponse : ne pas l'afficher.
            "text": "" if texte == "…" else texte,
            "tools": _tools(message.tool_log),
            # ⚠️ `net_tokens` est ce que le téléphone doit AFFICHER ; le total
            # est servi pour qui veut la charge brute, mais il additionne le
            # contexte relu (~93 % du volume) et se lit très mal. Les deux
            # voyagent, l'app choisit — et elle choisit la même chose que le
            # Cockpit, sinon les deux écrans se contredisent.
            "usage": {
                "input_tokens": message.input_tokens,
                "output_tokens": message.output_tokens,
                "cache_read_tokens": message.cache_read_tokens,
                "cache_write_tokens": message.cache_write_tokens,
                "net_tokens": message.net_tokens,
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
            return _json({"error": "conversation introuvable"}, 404)
        session.write({"active": False})
        return _json({"ok": True})
