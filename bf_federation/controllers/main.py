"""Les deux portes de la fédération : le jumelage (par code) et la boîte de réception (signée).

Tout tourne en superutilisateur sous un contexte qui ne renvoie rien au pair et
ne notifie personne par courriel. Rien d'arbitraire n'est exécuté : chaque
genre de message a sa méthode, et un genre inconnu est refusé. Les erreurs
rendues au pair sont génériques : ni nom de projet, ni existence d'un identifiant.
"""
import json
import logging

from psycopg2 import IntegrityError

from odoo import SUPERUSER_ID, fields, http
from odoo.http import request

from ..models import transport

_logger = logging.getLogger(__name__)
MAX_BODY = 8 * 1024 * 1024


def _json(data, status=200):
    return request.make_json_response(data, status=status)


def _env(peer=None):
    env = request.env(user=SUPERUSER_ID, context=dict(request.env.context, federation_inbound=True))
    if peer is not None and peer.company_id:
        env = env(context=dict(env.context, allowed_company_ids=[peer.company_id.id]))
    return env


def _read_body():
    if (request.httprequest.content_length or 0) > MAX_BODY:
        return None, None
    raw = request.httprequest.get_data() or b""
    if len(raw) > MAX_BODY:
        return None, None
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw, None


class FederationController(http.Controller):

    @http.route("/federation/v1/handshake", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False, max_content_length=MAX_BODY)
    def handshake(self, **kw):
        raw, payload = _read_body()
        if raw is None:
            return _json({"ok": False, "error": "charge trop grosse"}, 413)
        if not isinstance(payload, dict) or payload.get("protocol") != transport.PROTOCOL:
            return _json({"ok": False, "error": "charge invalide"}, 422)
        env = _env()
        try:
            peer, part = env["federation.peer"]._accept_handshake(
                payload.get("code"), payload.get("part"), payload.get("uuid"),
                payload.get("base_url"), payload.get("name"))
        except Exception:  # noqa: BLE001
            _logger.exception("federation: handshake failed")
            peer, part = None, None
        if not peer:
            _logger.warning("federation: handshake refused from %s", request.httprequest.remote_addr)
            return _json({"ok": False, "error": "invitation refusée"}, 403)
        me = env["federation.peer"].with_company(peer.company_id)
        return _json({"ok": True, "uuid": peer.sudo().uuid, "part": part,
                      "name": me._our_name(), "base_url": me._our_base_url()})

    def _authenticate(self, raw):
        headers = request.httprequest.headers
        env = _env()
        peer = env["federation.peer"].search(
            [("uuid", "=", headers.get(transport.HEADER_PEER) or ""), ("state", "=", "active")], limit=1)
        nonce = headers.get(transport.HEADER_NONCE) or ""
        if not peer or not peer.secret or not transport.signature_ok(
                peer.secret, headers.get(transport.HEADER_TIMESTAMP), nonce, raw,
                headers.get(transport.HEADER_SIGNATURE)):
            _logger.warning("federation: request refused from %s", request.httprequest.remote_addr)
            return None, _json({"ok": False, "error": "refusé"}, 401)
        try:
            with env.cr.savepoint():
                env["federation.nonce"].create({"peer_id": peer.id, "nonce": nonce[:128]})
        except IntegrityError:
            return None, _json({"ok": False, "error": "message déjà reçu"}, 409)
        return peer, None

    @http.route("/federation/v1/ping", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False, max_content_length=MAX_BODY)
    def ping(self, **kw):
        raw, _payload = _read_body()
        if raw is None:
            return _json({"ok": False, "error": "charge trop grosse"}, 413)
        peer, err = self._authenticate(raw)
        if err:
            return err
        peer.write({"last_ping": fields.Datetime.now()})
        return _json({"ok": True, "name": _env(peer)["federation.peer"].with_company(peer.company_id)._our_name()})

    def _dispatch(self, env, peer, kind, sender_ref, data):
        """Un genre, une méthode. Rend (charge de réponse, code HTTP)."""
        Link = env["federation.link"]
        if kind == "ping":
            return {"ok": True, "name": env["federation.peer"].with_company(peer.company_id)._our_name()}, 200
        if kind == "task.share":
            if not sender_ref:
                return {"ok": False, "error": "référence absente"}, 422
            link = Link._receive_share(peer, str(sender_ref)[:64], data)
            return {"ok": True, "ref": str(link.task_id.id), "url": link.task_id._federation_card()["url"]}, 200
        link = Link.with_context(active_test=False).search(
            [("peer_id", "=", peer.id), ("remote_ref", "=", str(sender_ref or "")[:64])], limit=1)
        if not link:
            return {"ok": False, "error": "lien inconnu"}, 404
        if kind == "task.card":
            link._apply_card(data)
        elif kind == "task.state":
            link._apply_state(data.get("state"))
        elif kind == "task.day":
            if link._apply_day(data.get("day")) is False and transport.valid_day(data.get("day")) is None:
                return {"ok": False, "error": "charge invalide"}, 422
        elif kind == "message.new":
            message = link._apply_message(data)
            return {"ok": True, "ref": str(message.id)}, 200
        elif kind == "link.archive":
            link._apply_archive(data.get("reason"))
        elif kind == "link.restore":
            link._apply_restore()
        elif kind == "mirror.dropped":
            link._apply_mirror_dropped(data.get("reason"))
        else:
            return {"ok": False, "error": "genre inconnu"}, 422
        return {"ok": True, "ref": str(link.task_id.id)}, 200

    @http.route("/federation/v1/inbox", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False, max_content_length=MAX_BODY)
    def inbox(self, **kw):
        raw, envelope = _read_body()
        if raw is None:
            return _json({"ok": False, "error": "charge trop grosse"}, 413)
        peer, err = self._authenticate(raw)
        if err:
            return err
        if not isinstance(envelope, dict) or envelope.get("protocol") != transport.PROTOCOL:
            return _json({"ok": False, "error": "charge invalide"}, 422)
        kind = envelope.get("kind")
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        sender_ref = envelope.get("sender_ref")
        if sender_ref is not None and not isinstance(sender_ref, (str, int)):
            return _json({"ok": False, "error": "charge invalide"}, 422)
        env = _env(peer)
        peer = peer.with_env(env).with_company(peer.company_id)
        try:
            # Un échec ne laisse rien à moitié fait chez le receveur.
            with env.cr.savepoint():
                payload, status = self._dispatch(env, peer, kind, sender_ref, data)
        except Exception:  # noqa: BLE001
            _logger.exception("federation: inbound %s from peer %s failed", kind, peer.id)
            return _json({"ok": False, "error": "refusé par le receveur"}, 422)
        return _json(payload, status)
