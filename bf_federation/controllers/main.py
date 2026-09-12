"""Les deux portes de la fédération : le jumelage (par code) et la boîte de réception (signée).

Tout tourne en superutilisateur sous un contexte qui ne renvoie rien au pair et
ne notifie personne par courriel. Rien d'arbitraire n'est exécuté : chaque
genre de message a sa méthode, et un genre inconnu est refusé.
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


def _env():
    return request.env(user=SUPERUSER_ID, context=dict(request.env.context, federation_inbound=True))


def _read_body():
    raw = request.httprequest.get_data() or b""
    if len(raw) > MAX_BODY:
        return None, None
    try:
        return raw, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw, None


class FederationController(http.Controller):

    @http.route("/federation/v1/handshake", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def handshake(self, **kw):
        raw, payload = _read_body()
        if not isinstance(payload, dict) or payload.get("protocol") != transport.PROTOCOL:
            return _json({"ok": False, "error": "charge invalide"}, 422)
        env = _env()
        peer = env["federation.peer"]._accept_handshake(
            payload.get("code"), payload.get("secret"), payload.get("uuid"), payload.get("base_url"), payload.get("name"))
        if not peer:
            _logger.warning("federation: handshake refused from %s", request.httprequest.remote_addr)
            return _json({"ok": False, "error": "invitation inconnue ou expirée"}, 403)
        return _json({"ok": True, "uuid": peer.uuid, "name": env["federation.peer"]._our_name(),
                      "base_url": env["federation.peer"]._our_base_url()})

    def _authenticate(self, raw):
        headers = request.httprequest.headers
        env = _env()
        peer = env["federation.peer"].search([("uuid", "=", headers.get(transport.HEADER_PEER) or ""), ("state", "=", "active")], limit=1)
        if not peer or not peer.secret:
            return None, _json({"ok": False, "error": "pair inconnu"}, 401)
        nonce = headers.get(transport.HEADER_NONCE) or ""
        if not transport.signature_ok(peer.secret, headers.get(transport.HEADER_TIMESTAMP), nonce, raw,
                                      headers.get(transport.HEADER_SIGNATURE)):
            _logger.warning("federation: bad signature from %s for peer %s", request.httprequest.remote_addr, peer.id)
            return None, _json({"ok": False, "error": "signature refusée"}, 401)
        try:
            with env.cr.savepoint():
                env["federation.nonce"].create({"peer_id": peer.id, "nonce": nonce})
        except IntegrityError:
            return None, _json({"ok": False, "error": "message déjà reçu"}, 409)
        return peer, None

    @http.route("/federation/v1/ping", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def ping(self, **kw):
        raw, _payload = _read_body()
        peer, err = self._authenticate(raw)
        if err:
            return err
        peer.write({"last_ping": fields.Datetime.now()})
        env = _env()
        return _json({"ok": True, "name": env["federation.peer"]._our_name()})

    @http.route("/federation/v1/inbox", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
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
        env = _env()
        Link = env["federation.link"]
        sender_ref = envelope.get("sender_ref")
        if kind == "ping":
            return _json({"ok": True, "name": env["federation.peer"]._our_name()})
        if kind == "task.share":
            if not sender_ref:
                return _json({"ok": False, "error": "référence absente"}, 422)
            link = Link._receive_share(peer, sender_ref, data)
            return _json({"ok": True, "ref": str(link.task_id.id), "url": link.task_id._federation_card()["url"]})
        link = Link.with_context(active_test=False).search(
            [("peer_id", "=", peer.id), ("remote_ref", "=", str(sender_ref or ""))], limit=1)
        if not link:
            return _json({"ok": False, "error": "lien inconnu"}, 404)
        if kind == "task.card":
            link._apply_card(data)
        elif kind == "task.state":
            link._apply_state(data.get("state"))
        elif kind == "task.day":
            link._apply_day(data.get("day"), data.get("tz"))
        elif kind == "message.new":
            message = link._apply_message(data)
            return _json({"ok": True, "ref": str(message.id)})
        elif kind == "link.archive":
            link._apply_archive(data.get("reason"))
        elif kind == "link.restore":
            link._apply_restore()
        else:
            return _json({"ok": False, "error": "genre inconnu"}, 422)
        return _json({"ok": True, "ref": str(link.task_id.id)})
