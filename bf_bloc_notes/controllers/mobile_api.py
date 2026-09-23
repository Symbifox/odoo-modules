"""API à jeton du bloc-notes, pour Symbifox Mobile.

Même patron que ``bf_capture`` et ``bf_speech`` : jeton porteur de l'appareil,
``/ping`` public qui annonce la capacité, tout le reste en ``auth="public"``
gardé par le jeton. Le travail est délégué à ``bf.note._mobile_*``, la couche
que la page ``/notes`` emprunte aussi : l'application et la page ne peuvent pas
diverger.

⚠️ Le jeton est reconnu sans dépendre du module qui l'a émis. Les deux moitiés
de l'application (messages, courriel) sont indépendantes par conception :
dépendre de l'une exclurait l'autre.

⚠️ Pourquoi ``request.update_env`` et pas ``sudo()`` : la règle
d'enregistrement du bloc-notes (auteur, ou note partagée en lecture seule) est
la seule garde qui compte ici, et elle ne s'applique qu'à l'usager réel.
"""

import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.bf_note_mobile import ACTIONS

_logger = logging.getLogger(__name__)

BASE = "/bf_bloc_notes/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

#: Corps JSON le plus gros accepté. Le texte d'une note est plafonné à 100 000
#: caractères par le modèle ; ceci refuse avant d'analyser une charge hors sujet.
MAX_BODY = 1024 * 1024


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _appareil_acceptable(device):
    """Un jeton valide ne suffit pas : l'usager doit être actif et interne.

    Un appareil dont l'usager est ARCHIVÉ garderait sinon sa porte au nom de
    quelqu'un qui est parti, et un compte de portail n'a pas de bloc-notes.
    """
    return bool(device) and device.user_id.active and not device.user_id.share


def _device():
    header = request.httprequest.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else None
    if not token:
        return None
    for model in _DEVICE_MODELS:
        if model not in request.env:
            continue
        device = request.env[model].sudo()._resolve(token)
        if _appareil_acceptable(device):
            return device
    return None


def _authed():
    device = _device()
    if not device:
        return None
    if "last_seen" in device._fields:
        device.sudo().write({"last_seen": fields.Datetime.now()})
    request.update_env(user=device.user_id.id)
    # La langue de l'usager, pour que les phrases rendues à l'application
    # (`message`, `detail`) soient dans la sienne et pas dans celle du serveur.
    lang = device.user_id.lang
    if lang:
        request.update_context(lang=lang)
    return device


def _corps():
    """Le corps JSON d'un POST, ou lève ``UserError``."""
    data = request.httprequest.get_data(cache=False) or b""
    if len(data) > MAX_BODY:
        raise UserError(_("Request too large."))
    if not data.strip():
        return {}
    try:
        corps = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UserError(_("Unreadable JSON."))
    if not isinstance(corps, dict):
        raise UserError(_("A JSON object is expected."))
    return corps


def _guarded(fn):
    """Rend l'erreur en JSON, et ANNULE ce que le geste avait déjà écrit.

    🔴 Une route ``type="http"`` qui rend une réponse normale est COMMITÉE par
    Odoo, même quand cette réponse dit « erreur ». Sans le retour en arrière,
    un geste refusé à mi-chemin (tâche créée, lien refusé ensuite) laisserait
    sa première moitié en base alors que l'application affiche un échec.
    """
    try:
        return fn()
    except AccessError as exc:
        request.env.cr.rollback()
        return _json({"error": "forbidden", "detail": str(exc)}, 403)
    except UserError as exc:
        request.env.cr.rollback()
        return _json({"error": "bad_request", "detail": str(exc)}, 400)
    except Exception:  # noqa: BLE001 — jamais de trace sur un téléphone
        request.env.cr.rollback()
        _logger.exception("API bloc-notes : erreur inattendue")
        return _json({"error": "server_error"}, 500)


def _note_ou_404(note_id):
    note = request.env["bf.note"]._mobile_browse(note_id)
    if not note:
        return None, _json({"error": "not_found"}, 404)
    return note, None


class BfNoteMobileApi(http.Controller):

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sans cette sonde, l'application n'affiche pas l'écran Notes."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_bloc_notes")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_bloc_notes",
            "api": 1,
            "version": module.installed_version or "",
            "enabled": True,
            "actions": list(ACTIONS),
        })

    @http.route(f"{BASE}/notes", type="http", auth="public", methods=["GET", "POST"],
                csrf=False, save_session=False)
    def notes(self, **kw):
        """GET : la liste (``limit``, ``offset``, ``archived``, ``q``, ``since``).
        POST : crée une note, idempotent sur ``client_uuid``."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        Note = request.env["bf.note"]

        def run():
            if request.httprequest.method == "POST":
                resultat = Note._mobile_create(_corps())
                return _json({"ok": True, **resultat},
                             201 if resultat["created"] else 200)
            resultat = Note._mobile_list(
                limit=kw.get("limit") or 50,
                offset=kw.get("offset") or 0,
                archived=kw.get("archived") in ("1", "true", "True"),
                query=kw.get("q"),
                since=kw.get("since"),
            )
            return _json({"ok": True, **resultat})

        return _guarded(run)

    @http.route(f"{BASE}/notes/<int:note_id>", type="http", auth="public",
                methods=["GET", "POST"], csrf=False, save_session=False)
    def note(self, note_id, **kw):
        """GET : une note. POST : modifie titre, texte, épingle, couleur."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            note, refus = _note_ou_404(note_id)
            if refus:
                return refus
            if request.httprequest.method == "GET":
                return _json({"ok": True, "note": note._mobile_payload()})
            resultat = note._mobile_update(_corps())
            if resultat["conflict"]:
                return _json({"error": "conflict", "note": resultat["note"]}, 409)
            return _json({"ok": True, "note": resultat["note"]})

        return _guarded(run)

    @http.route(f"{BASE}/notes/<int:note_id>/action", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def action(self, note_id, **kw):
        """Un geste rapide : ``{"action": "archive"}``, ``{"action": "activity",
        "days": 1}``, ``{"action": "task", "project_id": 12}``, …"""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            note, refus = _note_ou_404(note_id)
            if refus:
                return refus
            corps = _corps()
            resultat = note._mobile_action(corps.get("action"), corps)
            return _json({"ok": True, **resultat})

        return _guarded(run)

    @http.route(f"{BASE}/projets", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def projets(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json({
            "ok": True,
            "projets": request.env["bf.note"]._mobile_projects(kw.get("q")),
        }))

    @http.route(f"{BASE}/cibles", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def cibles(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json({
            "ok": True,
            "groupes": request.env["bf.note"]._mobile_targets(kw.get("q")),
        }))
