"""Surface mobile de l'agenda — le patron maison, sans écart.

Même forme que les autres surfaces mobiles de la maison, ``bf_claude_chat``
compris : jeton porteur de l'app, ``/ping`` public qui annonce la capacité,
tout le reste en
``auth="public"`` gardé par le jeton, et la collecte déléguée aux modèles pour
qu'elle passe par les droits de l'appelant.

⚠️ Le jeton est reconnu sans dépendre du module qui l'a émis. Les deux moitiés
de l'app (messages, courriel) sont indépendantes par conception : dépendre de
l'une exclurait l'autre.

⚠️ Aucune route ici ne fait partir d'invitation sans qu'on le lui demande.
(Le suivi d'Odoo, lui, notifie les abonnés d'une tâche qu'on modifie, comme au
bureau : ce n'est pas ce module qui le déclenche.) La confirmation de présence
écrit l'état et pose une note interne, là où
``do_accept`` d'Odoo publie sous le sous-type « Invitation », qui n'est pas
interne et poste donc aux abonnés. Sur un statutaire client, confirmer depuis
un téléphone aurait écrit au client. La seule exception est ``/event/attendees``
avec ``notify`` à vrai, un choix explicite de la personne, fermé par défaut.
"""

import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

_logger = logging.getLogger(__name__)

BASE = "/bf_calendar/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

# Les seuls états qu'un client peut poser. Filtré contre cette liste plutôt que
# passé tel quel : `write({'state': ...})` accepterait n'importe quelle chaîne
# et laisserait une fiche participant dans un état qu'aucune vue ne sait lire.
_RSVP_STATES = ("accepted", "declined", "tentative")

# Reports offerts. Repris de `SNOOZE_PRESETS` de `bf_email_management`, en
# valeurs plutôt qu'en import : un préréglage ajouté là-bas ne doit pas changer
# ce que l'app affiche sans qu'on l'ait voulu.
_SNOOZE_MINUTES = (5, 15, 60, 180)

# Les accusés d'idempotence : voir ``models/mobile_receipt.py``.
_RECU = "bf.calendar.mobile.receipt"


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
        # Un appareil valide dont l'USAGER est archivé garderait sinon un
        # agenda et le droit d'y répondre, en son nom.
        if device and device.user_id.active:
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
    """Rend les erreurs en JSON plutôt qu'en page d'erreur Odoo."""
    try:
        return fn()
    except AccessError as exc:
        return _json({"error": "forbidden", "detail": str(exc)}, 403)
    except UserError as exc:
        return _json({"error": "bad_request", "detail": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        _logger.exception("API agenda mobile : erreur inattendue")
        return _json({"error": "server_error"}, 500)


def _corps():
    """Le corps JSON s'il est un objet, sinon ``{}`` : une liste ou un nombre
    ne doit pas faire un 500 au premier ``.get``."""
    data = _body()
    return data if isinstance(data, dict) else {}


def _entier(valeur):
    """Un identifiant du corps JSON, ou 0. `int("abc")` ne doit pas faire un 500."""
    try:
        return int(valeur or 0)
    except (TypeError, ValueError):
        return 0


def _activite(data):
    """L'activité nommée par ``activity_id``, lue avec les droits de l'appelant.

    Une activité ARCHIVÉE (type « garder les activités faites ») est une
    activité déjà faite : 404, sinon un rejeu sans accusé la referait, avec un
    second message « fait » et ses notifications (relecture adverse).
    """
    return request.env["mail.activity"].browse(
        _entier((data or {}).get("activity_id"))).exists().filtered("active")


def _sauf_echec(run):
    """Le geste dans un point de reprise.

    🔴 ``_guarded`` change une exception en réponse JSON, et une réponse
    rendue est COMMITÉE par Odoo : ce qu'un geste a écrit avant d'échouer
    resterait en base. Le point de reprise défait le geste entier, puis
    l'exception remonte à ``_guarded`` comme avant.
    """
    with request.env.cr.savepoint():
        return run()


def _idempotent(route, brut, run):
    """Le geste une seule fois par ``client_uuid``.

    Sans ``client_uuid``, rien ne change : une ancienne version de l'app
    n'en envoie pas. Avec, l'accusé déjà posé rend la réponse d'origine telle
    quelle, plus ``"replay": true`` ; sinon le geste s'exécute et, s'il
    réussit, l'accusé est posé dans la même transaction.
    """
    if brut is None or brut == "":
        return run()
    Recu = request.env[_RECU].sudo()
    try:
        cle = Recu._normalize(brut)
    except ValueError:
        return _json({"error": "invalid_client_uuid"}, 400)
    uid = request.env.uid
    deja = Recu._acquire(uid, cle)
    if deja is not None:
        route_origine, charge = deja
        if route_origine and route_origine != route:
            return _json({"error": "invalid_client_uuid",
                          "detail": "client_uuid already used by another request"}, 400)
        return _json({**charge, "replay": True})
    reponse = run()
    if 200 <= reponse.status_code < 300:
        Recu._record(uid, cle, route, reponse.get_data(as_text=True))
    return reponse


class BfCalendarMobileApi(http.Controller):

    # ------------------------------------------------------------------
    # Capacité
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sans cette sonde, l'app n'affiche jamais l'onglet."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_calendar_mobile")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_calendar_mobile",
            # api 2 : couleurs d'Odoo, exclusions, création d'événement et de
            # tâche, modification et complétion. api 3 : rappels configurés,
            # participants modifiables, recherche de contacts, tâche par
            # identifiant. api 4 : recherche dans mes tâches ouvertes
            # (`/tasks/search`). api 5 : modifier une rencontre et ses
            # rappels (`/event/write`, `/alarms`). api 6 : tâche privée, sans
            # projet (`private: true` sur /task/create). api 7 : mes
            # activités (`/activities`, fait, reporter, planifier),
            # description et commentaire de tâche. api 8 : `skip_agenda` et
            # `skip_dashboard` acceptés sur /event/create, et l'avis « agenda »
            # poussé quand une rencontre change. Une app plus ancienne lit
            # le nombre et ignore ce qu'elle ne connaît pas.
            "api": 8,
            # `client_uuid` accepté sur /event/create, /task/create,
            # /event/write, /task/comment et les quatre routes d'activité.
            "idempotency": 1,
            "version": module.installed_version or "",
            "enabled": True,
        })

    @http.route(f"{BASE}/config", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def config(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            env = request.env
            return _json({
                "ok": True,
                "user": env.user.display_name,
                # Le fuseau du COMPTE, que l'app compare au sien : le compte
                # et l'appareil ne sont pas toujours dans le même, et un écart
                # muet ferait lire la grille à côté.
                "user_tz": env.user.tz or "",
                "snooze_minutes": list(_SNOOZE_MINUTES),
                "features": {
                    "meetings": "bf_agenda_state" in env["calendar.event"]._fields,
                    "tasks": True,
                    "rsvp": True,
                },
            })
        return _guarded(run)

    # ------------------------------------------------------------------
    # Agenda
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/events", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def events(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["calendar.event"].mobile_range(
                kw.get("from"), kw.get("to"))))

    @http.route(f"{BASE}/event", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def event(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            event = request.env["calendar.event"]._mobile_resolve(
                kw.get("id"), kw.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            return _json({"ok": True, "event": event.mobile_detail()})
        return _guarded(run)

    # ------------------------------------------------------------------
    # Gestes sur le rappel
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/snooze", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def snooze(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            minutes = int(data.get("minutes") or 0)
            if minutes not in _SNOOZE_MINUTES:
                return _json({"error": "bad_request",
                              "detail": "Report non offert."}, 400)
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            result = request.env["calendar.attendee"].bf_snooze(
                event.id, minutes=minutes)
            return _json({"ok": True, "event_id": event.id, **result})
        return _guarded(run)

    @http.route(f"{BASE}/dismiss", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def dismiss(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            result = request.env["calendar.attendee"].bf_dismiss(event.id)
            return _json({"ok": True, "event_id": event.id, **result})
        return _guarded(run)

    @http.route(f"{BASE}/rsvp", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def rsvp(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            state = (data.get("state") or "").strip()
            if state not in _RSVP_STATES:
                return _json({"error": "bad_request",
                              "detail": "État de présence inconnu."}, 400)
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            attendee = event._mobile_attendee()
            if not attendee:
                return _json({"error": "bad_request",
                              "detail": "Vous ne participez pas à cette "
                                        "rencontre."}, 400)
            attendee.write({"state": state})
            # Trace interne seulement. Voir l'en-tête du fichier : le
            # sous-type « Invitation » d'Odoo poste aux abonnés.
            labels = {"accepted": _("a confirmé sa présence"),
                      "declined": _("a décliné"),
                      "tentative": _("répond « peut-être »")}
            event.message_post(
                body=_("%(who)s %(what)s depuis l'application mobile.",
                       who=request.env.user.display_name,
                       what=labels[state]),
                subtype_xmlid="mail.mt_note",
            )
            return _json({"ok": True, "event_id": event.id, "state": state})
        return _guarded(run)

    # ------------------------------------------------------------------
    # Écrire sur l'agenda
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/calendars", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def calendars(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json({
            "ok": True,
            "calendars": request.env["calendar.event"].mobile_calendars(),
        }))

    @http.route(f"{BASE}/event/create", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def event_create(self, **kw):
        """JSON de ``mobile_create``, plus ``client_uuid`` facultatif."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _body()
        return _guarded(lambda: _idempotent(
            "/event/create", data.get("client_uuid") if isinstance(data, dict) else None,
            lambda: _json(request.env["calendar.event"].mobile_create(data))))

    @http.route(f"{BASE}/event/flags", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def event_flags(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            return _json(event.mobile_set_flags(
                skip_agenda=data.get("skip_agenda"),
                skip_dashboard=data.get("skip_dashboard")))
        return _guarded(run)

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/alarms", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def alarms(self, **kw):
        """Les rappels offerts, pour la création et la modification."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["calendar.event"].mobile_alarm_choices()))

    @http.route(f"{BASE}/event/write", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def event_write(self, **kw):
        """Changer l'heure, le titre, le lieu ou les rappels.

        Corps : ``event_id``, ``key``, ``values`` (liste blanche de
        ``mobile_write``), ``client_uuid`` facultatif.
        """
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _body()
        if not isinstance(data, dict):
            data = {}

        def run():
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            return _json(event.mobile_write(data.get("values") or {}))
        return _guarded(lambda: _idempotent(
            "/event/write", data.get("client_uuid"), run))

    @http.route(f"{BASE}/partners", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def partners(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json({
            "ok": True,
            "partners": request.env["calendar.event"].mobile_partners(
                kw.get("q"), kw.get("limit") or 20),
        }))

    @http.route(f"{BASE}/event/attendees", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def event_attendees(self, **kw):
        """`{event_id|key, add: [ids], remove: [ids], notify: bool}`.

        ⚠️ `notify` est la SEULE porte de ce module par laquelle un courriel
        peut partir, et elle est fermée par défaut. Voir l'en-tête.
        """
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            event = request.env["calendar.event"]._mobile_resolve(
                data.get("event_id"), data.get("key"))
            if not event:
                return _json({"error": "not_found"}, 404)
            return _json(event.mobile_set_attendees(
                add_ids=data.get("add") or [],
                remove_ids=data.get("remove") or [],
                notify=bool(data.get("notify", False))))
        return _guarded(run)

    # ------------------------------------------------------------------
    # Échéances
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/tasks", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def tasks(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        undated = str(kw.get("undated") or "").lower() in ("1", "true", "yes")
        return _guarded(lambda: _json(
            request.env["project.task"].mobile_todo(
                kw.get("from"), kw.get("to"), include_undated=undated)))

    @http.route(f"{BASE}/tasks/search", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def tasks_search(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["project.task"].mobile_search(
                kw.get("q"), limit=kw.get("limit"))))

    @http.route(f"{BASE}/task", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def task(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            task = request.env["project.task"].browse(
                int(kw.get("id") or 0)).exists()
            if not task:
                return _json({"error": "not_found"}, 404)
            return _json(task.mobile_detail())
        return _guarded(run)

    @http.route(f"{BASE}/task_counts", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def task_counts(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["project.task"].mobile_deadline_counts(
                kw.get("from"), kw.get("to"), tz=kw.get("tz"))))

    # ------------------------------------------------------------------
    # Écrire sur les tâches
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/task/options", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def task_options(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["project.task"].mobile_options(kw.get("project_id"))))

    @http.route(f"{BASE}/task/write", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def task_write(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            task = request.env["project.task"].browse(
                int(data.get("task_id") or 0)).exists()
            if not task:
                return _json({"error": "not_found"}, 404)
            return _json(task.mobile_write(data.get("values") or {}))
        return _guarded(run)

    @http.route(f"{BASE}/task/done", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def task_done(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            data = _body()
            task = request.env["project.task"].browse(
                int(data.get("task_id") or 0)).exists()
            if not task:
                return _json({"error": "not_found"}, 404)
            return _json(task.mobile_done(bool(data.get("done", True))))
        return _guarded(run)

    @http.route(f"{BASE}/task/create", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def task_create(self, **kw):
        """JSON de ``mobile_create``, plus ``client_uuid`` facultatif."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _body()
        return _guarded(lambda: _idempotent(
            "/task/create", data.get("client_uuid") if isinstance(data, dict) else None,
            lambda: _json(request.env["project.task"].mobile_create(data))))

    @http.route(f"{BASE}/task/comment", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def task_comment(self, **kw):
        """Une note interne au fil de la tâche."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _corps()

        def run():
            task = request.env["project.task"].browse(
                _entier(data.get("task_id"))).exists()
            if not task:
                return _json({"error": "not_found"}, 404)
            return _json(task.mobile_comment(data.get("body")))
        return _guarded(lambda: _idempotent(
            "/task/comment", data.get("client_uuid"), lambda: _sauf_echec(run)))

    # ------------------------------------------------------------------
    # Activités
    # ------------------------------------------------------------------

    @http.route(f"{BASE}/activities", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def activities(self, **kw):
        """Mes activités ouvertes jusqu'à ``to`` (AAAA-MM-JJ), retards compris."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["mail.activity"].mobile_mine(kw.get("to"))))

    @http.route(f"{BASE}/activity/types", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def activity_types(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json(
            request.env["mail.activity"].mobile_types(kw.get("res_model") or None)))

    @http.route(f"{BASE}/activity/done", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def activity_done(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _corps()

        def run():
            activite = _activite(data)
            if not activite:
                return _json({"error": "not_found"}, 404)
            return _json(activite.mobile_done(data.get("feedback")))
        return _guarded(lambda: _idempotent(
            "/activity/done", data.get("client_uuid"), lambda: _sauf_echec(run)))

    @http.route(f"{BASE}/activity/reschedule", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def activity_reschedule(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _corps()

        def run():
            activite = _activite(data)
            if not activite:
                return _json({"error": "not_found"}, 404)
            return _json(activite.mobile_reschedule(data.get("date")))
        return _guarded(lambda: _idempotent(
            "/activity/reschedule", data.get("client_uuid"), lambda: _sauf_echec(run)))

    @http.route(f"{BASE}/activity/create", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def activity_create(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        data = _corps()
        return _guarded(lambda: _idempotent(
            "/activity/create", data.get("client_uuid"),
            lambda: _sauf_echec(lambda: _json(request.env["mail.activity"].mobile_create(data)))))
