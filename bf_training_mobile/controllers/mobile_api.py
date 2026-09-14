"""Surface mobile du registre de formation — le patron maison, sans écart.

Même forme que ``bf_calendar_mobile`` et ``bf_hosting_mobile`` : jeton porteur de
l'app, ``/ping`` public qui annonce la capacité, tout le reste en
``auth="public"`` gardé par le jeton, et la collecte déléguée aux modèles pour
qu'elle passe par les droits de l'appelant.

⚠️ Le jeton est reconnu sans dépendre du module qui l'a émis. Les moitiés de
l'app sont indépendantes par conception : dépendre de l'une exclurait l'autre.

🔴 **Aucune route ne prend d'identifiant d'employé.** La personne est déduite du
jeton, jamais de ce que le client envoie. Un paramètre qu'on n'accepte pas est un
paramètre qu'on ne peut pas forger — et c'est la seule garde qui tienne, parce
que le registre d'une organisation contient précisément ce que les gens n'ont pas
à savoir les uns des autres.
"""
import json
import logging

from odoo import fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/bf_training/mobile/v1"

#: Les modèles d'appareil connus, par ordre d'apparition. Repris en valeurs
#: plutôt qu'en dépendance : ce module n'a pas à exiger la messagerie SMS pour
#: servir un registre de formation.
_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def appareil_utilisable(device):
    """Un appareil résolu donne-t-il droit au registre ?

    🔴 Extrait de `_device()` À DESSEIN, pour être éprouvable. La garde vivait
    dans une fonction qui exige une requête HTTP et un module émetteur de
    jetons — dont celui-ci ne dépend volontairement pas — et une mutation qui
    la supprimait s'échappait donc sans qu'aucun essai bronche. Une garde qu'on
    ne peut pas éprouver seule n'est pas une garde, c'est une intention.

    Ce qu'elle refuse : un appareil parfaitement valide dont l'USAGER est
    archivé. Il garderait sinon le dossier de formation d'une personne partie,
    en son nom.
    """
    return bool(device and device.user_id and device.user_id.active)


def _device():
    header = request.httprequest.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else None
    if not token:
        return None
    for model in _DEVICE_MODELS:
        if model not in request.env:
            continue
        device = request.env[model].sudo()._resolve(token)
        if appareil_utilisable(device):
            return device
    return None


def _authed():
    """Bascule l'environnement sur l'usager de l'appareil, ou rend None."""
    device = _device()
    if not device:
        return None
    if "last_seen" in device._fields:
        device.sudo().write({"last_seen": fields.Datetime.now()})
    request.update_env(user=device.user_id.id)
    return device


def _guarded(fn):
    try:
        return fn()
    except AccessError as exc:
        return _json({"error": "forbidden", "detail": str(exc)}, 403)
    except UserError as exc:
        return _json({"error": "bad_request", "detail": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        _logger.exception("API registre de formation mobile : erreur inattendue")
        return _json({"error": "server_error"}, 500)


def _mon_employe():
    """La fiche d'employé de l'appelant, ou un ensemble vide.

    ⚠️ Vide n'est pas une panne : quelqu'un qui n'est pas au registre n'a rien à
    y voir, et lui rendre une erreur ferait croire à un défaut de l'app.
    """
    return request.env["hr.employee"].search(
        [("user_id", "=", request.env.user.id)], limit=1)


class TrainingMobileApi(http.Controller):

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_training_mobile")], limit=1)
        return _json({
            "ok": True,
            # api 1 : mes obligations, mes réalisations, mes assignations, et
            # l'accusé de lecture d'une assignation. Une app plus ancienne lit
            # le nombre et ignore ce qu'elle ne connaît pas.
            "api": 1,
            "version": module.installed_version or "",
            "enabled": True,
        })

    @http.route(f"{BASE}/summary", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def summary(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            employe = _mon_employe()
            if not employe:
                return _json({"ok": True, "registered": False, "counts": {}})
            Obligation = request.env["bf.training.obligation"]
            en_retard = Obligation.search_count(
                [("employee_id", "=", employe.id), ("state", "=", "overdue")])
            bientot = Obligation.search_count(
                [("employee_id", "=", employe.id), ("state", "=", "expiring")])
            a_faire = Obligation.search_count(
                [("employee_id", "=", employe.id), ("state", "=", "pending")])
            return _json({
                "ok": True,
                "registered": True,
                "employee": employe.display_name,
                "counts": {
                    "overdue": en_retard,
                    "expiring": bientot,
                    "pending": a_faire,
                },
            })
        return _guarded(run)

    @http.route(f"{BASE}/obligations", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def obligations(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            employe = _mon_employe()
            if not employe:
                return _json({"ok": True, "obligations": []})
            # 🔴 Le domaine porte `employee_id` de l'appelant, et la lecture
            # passe quand même par les règles d'enregistrement : la garde est
            # double, parce qu'une seule se contourne le jour où quelqu'un
            # ajoute un paramètre à cette route.
            lignes = request.env["bf.training.obligation"].search(
                [("employee_id", "=", employe.id), ("state", "!=", "exempt")],
                order="due_date asc")
            return _json({"ok": True, "obligations": [{
                "id": o.id,
                "name": o.requirement_id.name,
                "activity": o.activity_id.display_name or None,
                "state": o.state,
                "due_date": o.due_date or None,
                "valid_until": o.valid_until or None,
                "hours_done": o.hours_done,
                "hours_required": o.hours_required,
            } for o in lignes]})
        return _guarded(run)

    @http.route(f"{BASE}/records", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def records(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            employe = _mon_employe()
            if not employe:
                return _json({"ok": True, "records": []})
            lignes = request.env["bf.training.record"].search(
                [("employee_id", "=", employe.id), ("state", "!=", "cancelled")],
                order="date_done desc")
            return _json({"ok": True, "records": [{
                "id": r.id,
                "activity": r.activity_id.display_name,
                "date_done": r.date_done or None,
                "hours": r.hours,
                "expiry_state": r.expiry_state or None,
                "date_expiry": r.date_expiry or None,
                # ⚠️ L'app montre ce qui manque plutôt qu'un simple « incomplet » :
                # la personne est souvent la seule à pouvoir le combler.
                "is_complete": r.is_complete,
                "missing": r.missing_info or None,
                "attachments": r.attachment_count,
            } for r in lignes]})
        return _guarded(run)

    @http.route(f"{BASE}/assignments", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def assignments(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            employe = _mon_employe()
            if not employe:
                return _json({"ok": True, "assignments": []})
            lignes = request.env["bf.training.assignment"].search(
                [("employee_id", "=", employe.id)], order="due_date asc")
            return _json({"ok": True, "assignments": [{
                "id": a.id,
                "activity": a.activity_id.display_name,
                "state": a.state,
                "due_date": a.due_date or None,
            } for a in lignes]})
        return _guarded(run)
