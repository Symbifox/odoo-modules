"""Surface mobile de la captation, au patron maison.

Même forme que ``bf_calendar_mobile`` : jeton porteur de l'app,
``/ping`` public qui annonce la capacité, tout le reste en ``auth="public"``
gardé par le jeton, et le travail délégué au modèle pour qu'il passe par les
droits de l'appelant.

⚠️ Le jeton est reconnu sans dépendre du module qui l'a émis. Les deux moitiés
de l'app (messages, courriel) sont indépendantes par conception : dépendre de
l'une exclurait l'autre.

⚠️ Le dépôt écrit dans le Nextcloud d'un compte de service, hors des droits de
l'appelant : la garde est donc explicite, un usager interne seulement. Un
compte portail avec un jeton d'appareil pourrait sinon poser un fichier dans le
dossier que le processeur de rencontres vide toutes les trente secondes.
"""

import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.bf_capture import ServiceIndisponible
from ..models.televersement import MORCEAU_BYTES, Conflit

_logger = logging.getLogger(__name__)

BASE = "/bf_capture/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

# Ce qu'un téléphone Android produit avec MediaRecorder, plus ce qu'un autre
# client raisonnable pourrait envoyer. La liste sert à refuser tôt ce qui n'est
# pas du son ; l'extension retenue, elle, est arbitrée par le modèle.
_TYPES = ("audio/", "video/mp4", "video/webm", "application/octet-stream")

# Les accusés d'idempotence : voir ``models/mobile_receipt.py``.
_RECU = "bf.capture.mobile.receipt"


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _appareil_acceptable(device):
    """Ce qu'un jeton valide ne suffit pas à autoriser.

    Un appareil dont l'usager est ARCHIVÉ garderait sinon sa surface au nom de
    quelqu'un qui est parti. Un usager PARTAGÉ (portail) n'a, lui, rien à
    déposer ici : le dépôt écrit dans le Nextcloud d'un compte de service, hors
    de ses droits, dans un dossier qu'un robot vide toutes les trente secondes.
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
    return device


def _guarded(fn):
    try:
        return fn()
    except Conflit as exc:
        # Hors séquence : la position où reprendre, pas un refus.
        return _json({"error": "offset", "recus": exc.recus}, 409)
    except ServiceIndisponible:
        # 🔴 Une panne n'est pas un refus. En 400, l'app retirait
        # l'enregistrement de sa file ; en 503, elle le garde et le rejoue.
        return _json({"error": "unavailable"}, 503)
    except AccessError as exc:
        return _json({"error": "forbidden", "detail": str(exc)}, 403)
    except UserError as exc:
        # ⚠️ Couvre aussi les `ValidationError` : les gardes de chemin de la
        # passerelle Nextcloud lèvent celle-là, et elle DÉRIVE de UserError
        # depuis Odoo 13. Vérifié plutôt que supposé (un essai le rejoue), et
        # l'ajouter explicitement ici ne changerait rien.
        return _json({"error": "bad_request", "detail": str(exc)}, 400)
    except Exception:  # noqa: BLE001
        _logger.exception("API captation : erreur inattendue")
        return _json({"error": "server_error"}, 500)


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
            # Le même identifiant pour deux gestes différents : rendre la
            # réponse de l'autre serait mentir sur ce qui a été fait.
            return _json({"error": "invalid_client_uuid",
                          "detail": "client_uuid already used by another request"}, 400)
        return _json({**charge, "replay": True})
    reponse = run()
    if 200 <= reponse.status_code < 300:
        Recu._record(uid, cle, route, reponse.get_data(as_text=True))
    return reponse


def _corps_json():
    """Le corps JSON, ou {} : un corps illisible finit en refus 400 par la
    validation du modèle, jamais en 500 que l'app rejouerait sans fin."""
    try:
        corps = json.loads(request.httprequest.get_data(as_text=True) or "{}")
    except ValueError:
        return {}
    return corps if isinstance(corps, dict) else {}


def _fichier():
    """Le téléversement, sous ``audio``. Rend (octets, nom) ou lève."""
    upload = request.httprequest.files.get("audio")
    if upload is None:
        raise UserError(_("Aucun fichier audio."))
    content_type = (upload.mimetype or "").lower()
    if content_type and not content_type.startswith(_TYPES):
        raise UserError(_("Format non reconnu : %s") % content_type)
    return upload.read(), upload.filename or "capture.m4a"


class BfCaptureMobileApi(http.Controller):

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sans cette sonde, l'app n'affiche pas l'enregistreur."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_capture")], limit=1)
        capture = request.env["bf.capture"].sudo()
        settings = capture._settings()
        return _json({
            "ok": True,
            "module": "bf_capture",
            "api": 1,
            "version": module.installed_version or "",
            # Un bouton qui ne peut pas aboutir vaut moins que pas de bouton.
            "enabled": capture.is_configured(),
            "memo_enabled": capture.transcription_disponible(),
            # `client_uuid` accepté sur /rencontre et /memo.
            "idempotency": 1,
            "max_bytes": settings["max_bytes"],
            "memo_max_bytes": settings["memo_max_bytes"],
            "dossier": settings["dossier"],
            # Téléverser un fichier média par morceaux, avec reprise.
            "televersement": 1,
            "televersement_max_bytes": capture._televersement_max(),
            "morceau_bytes": MORCEAU_BYTES,
        })

    @http.route(f"{BASE}/cibles", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def cibles(self, **kw):
        """Les rencontres auxquelles rattacher un enregistrement."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            avant = int(kw.get("heures_avant") or 6)
            apres = int(kw.get("heures_apres") or 12)
            return _json({
                "ok": True,
                "cibles": request.env["bf.capture"].cibles(
                    heures_avant=max(0, min(avant, 72)),
                    heures_apres=max(0, min(apres, 72)),
                ),
            })

        return _guarded(run)

    @http.route(f"{BASE}/rencontre", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def rencontre(self, **kw):
        """Multipart : ``audio`` + (``event_id`` | ``titre`` et ``debut``).

        ``client_uuid`` facultatif (champ du formulaire) : voir ``_idempotent``.
        """
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            contenu, nom_source = _fichier()
            resultat = request.env["bf.capture"].deposer_rencontre(
                contenu,
                nom_source=nom_source,
                event_id=kw.get("event_id") or None,
                titre=kw.get("titre") or None,
                debut=kw.get("debut") or None,
            )
            return _json({"ok": True, **resultat})

        return _guarded(lambda: _idempotent("/rencontre", kw.get("client_uuid"), run))

    @http.route(f"{BASE}/memo", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def memo(self, **kw):
        """Multipart : ``audio`` + ``titre`` facultatif. Rend la note créée.

        ``client_uuid`` facultatif (champ du formulaire) : voir ``_idempotent``.
        """
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            contenu, nom_source = _fichier()
            resultat = request.env["bf.capture"].deposer_memo(
                contenu, nom_source=nom_source, titre=kw.get("titre") or None)
            return _json({"ok": True, **resultat})

        return _guarded(lambda: _idempotent("/memo", kw.get("client_uuid"), run))

    # ── Téléversement par morceaux ───────────────────────
    @http.route(f"{BASE}/televersement/ouvrir", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def televersement_ouvrir(self, **kw):
        """JSON : nom, taille, et event_id ou titre et debut."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            corps = _corps_json()
            return _json({"ok": True, **request.env["bf.capture"].televersement_ouvrir(
                corps.get("nom"), corps.get("taille"),
                event_id=corps.get("event_id") or None,
                titre=corps.get("titre") or None,
                debut=corps.get("debut") or None,
            )})

        return _guarded(run)

    @http.route(f"{BASE}/televersement/morceau", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def televersement_morceau(self, **kw):
        """Corps brut : les octets. upload_id et offset dans l'URL."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            donnees = request.httprequest.get_data(cache=False)
            if len(donnees) > MORCEAU_BYTES:
                return _json({"error": "bad_request", "detail": "morceau trop gros"}, 413)
            recus = request.env["bf.capture"].televersement_morceau(
                kw.get("upload_id"), kw.get("offset") or 0, donnees)
            return _json({"ok": True, "recus": recus})

        return _guarded(run)

    @http.route(f"{BASE}/televersement/etat", type="http", auth="public",
                methods=["GET"], csrf=False, save_session=False)
    def televersement_etat(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _json({"ok": True, **request.env["bf.capture"].televersement_etat(
            kw.get("upload_id"))}))

    @http.route(f"{BASE}/televersement/terminer", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def televersement_terminer(self, **kw):
        """JSON : upload_id et client_uuid (voir _idempotent)."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        corps = _corps_json()

        def run():
            return _json({"ok": True, **request.env["bf.capture"].televersement_terminer(
                corps.get("upload_id"))})

        return _guarded(lambda: _idempotent("/televersement/terminer", corps.get("client_uuid"), run))

    @http.route(f"{BASE}/televersement/abandonner", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def televersement_abandonner(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        corps = _corps_json()
        return _guarded(lambda: _json({"ok": request.env["bf.capture"].televersement_abandonner(
            corps.get("upload_id"))}))
