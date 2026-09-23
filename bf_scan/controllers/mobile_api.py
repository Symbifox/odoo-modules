"""API à jeton de la numérisation, pour Symbifox Mobile.

La page ``/scan`` vit en session Odoo ; l'application, elle, n'a qu'un jeton
d'appareil. Cette API est la même porte, ouverte au jeton : chaque route
authentifie l'appareil, se met dans la peau de son usager, puis appelle la
MÊME méthode que la page. Les droits (groupe de facturation, enrichissement de
contacts, compte interne), les refus rédigés et la lecture de la facture ne sont
donc écrits qu'une fois : une API qui les recopierait divergerait au premier
correctif.

Différences avec la page, et pourquoi :

* **Multipart, pas du base64 dans du JSON.** Une photo de 300 Ko en base64
  pèse 400 Ko et doit tenir en mémoire deux fois ; le téléphone envoie le
  fichier tel quel, et c'est ici qu'il est encodé pour les méthodes de la page.
* **Codes HTTP.** La page lit un ``{"error": …}`` dans une réponse 200 ;
  l'application attend un 400 avec ``detail`` (même contrat que les autres API
  mobiles de la maison).
* **Retour en arrière sur refus.** Les méthodes de la page rendent leurs refus
  au lieu de les lever, et une réponse normale est commitée par Odoo : un dépôt
  refusé après la création du brouillon garderait le brouillon. L'API annule
  la transaction dès qu'une méthode rend une erreur.
"""

import base64
import json
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from .scan import TAILLE_MAX, ScanEtendu

_logger = logging.getLogger(__name__)

BASE = "/bf_scan/mobile/v1"

_DEVICE_MODELS = ("sms.archive.mobile.device", "bf.email.mobile.device")

MAX_JSON = 256 * 1024


def _json(data, status=200):
    return request.make_response(
        json.dumps(data, default=str),
        headers=[("Content-Type", "application/json; charset=utf-8")],
        status=status,
    )


def _appareil_acceptable(device):
    """Un jeton valide ne suffit pas : usager actif et interne seulement.

    Un appareil dont l'usager est ARCHIVÉ garderait sinon sa porte au nom de
    quelqu'un qui est parti ; un compte de portail n'a rien à numériser ici.
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
    if device.user_id.lang:
        request.update_context(lang=device.user_id.lang)
    return device


def _image():
    """(base64, nom) du fichier reçu sous ``image``, ou lève ``UserError``.

    Le type n'est PAS décidé ici : les méthodes de la page le décident sur les
    octets, et le nom du fichier ne décide de rien.
    """
    upload = request.httprequest.files.get("image")
    if upload is None:
        raise UserError(_("Aucune image reçue."))
    octets = upload.read(TAILLE_MAX + 1)
    if len(octets) > TAILLE_MAX:
        raise UserError(_(
            "Le fichier dépasse %s Mo. Reprenez-le en photo plutôt que de "
            "l'envoyer tel quel.") % (TAILLE_MAX // (1024 * 1024)))
    return base64.b64encode(octets).decode(), upload.filename or None


def _corps():
    data = request.httprequest.get_data(cache=False) or b""
    if len(data) > MAX_JSON:
        raise UserError(_("Requête trop volumineuse."))
    if not data.strip():
        return {}
    try:
        corps = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UserError(_("JSON illisible."))
    if not isinstance(corps, dict):
        raise UserError(_("Un objet JSON est attendu."))
    return corps


def _rendre(resultat):
    """Traduit la réponse d'une méthode de la page en réponse d'API."""
    if isinstance(resultat, dict) and resultat.get("error"):
        # 🔴 La méthode a RENDU son refus au lieu de le lever : sans ce retour
        # en arrière, ce qu'elle avait déjà écrit resterait en base.
        request.env.cr.rollback()
        return _json({"error": "bad_request", "detail": resultat["error"]}, 400)
    return _json({"ok": True, **(resultat or {})})


def _guarded(fn):
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
        _logger.exception("API numérisation : erreur inattendue")
        return _json({"error": "server_error"}, 500)


class BfScanMobileApi(http.Controller):

    @staticmethod
    def _page():
        """Le contrôleur de la page, dont on emprunte les gestes.

        ⚠️ Instancié à la demande : ses méthodes ne lisent que ``request``, qui
        porte déjà l'usager de l'appareil au moment de l'appel.
        """
        return ScanEtendu()

    @http.route(f"{BASE}/ping", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def ping(self, **kw):
        """Sans cette sonde, l'application n'affiche pas l'écran Scan."""
        module = request.env["ir.module.module"].sudo().search(
            [("name", "=", "bf_scan")], limit=1)
        return _json({
            "ok": True,
            "module": "bf_scan",
            "api": 1,
            "version": module.installed_version or "",
            "enabled": True,
            "max_bytes": TAILLE_MAX,
        })

    @http.route(f"{BASE}/capacites", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def capacites(self, **kw):
        """Les tuiles permises à CET usager : l'écran cache les autres, comme
        la page (une tuile grisée n'a pas la place d'expliquer trois droits)."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        page = self._page()
        return _guarded(lambda: _json({
            "ok": True,
            "carte": bool(page._may_scan()),
            "facture": bool(page._peut_facture()),
            "document": bool(page._peut_document()),
        }))

    @http.route(f"{BASE}/cibles", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def cibles(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)
        return _guarded(lambda: _rendre(self._page().document_cibles(query=kw.get("q"))))

    @http.route(f"{BASE}/document", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def document(self, **kw):
        """Multipart : ``image`` + ``titre``, ``rappel``, ``destination``,
        ``cible_model`` et ``cible_id`` facultatifs."""
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            image_b64, nom = _image()
            cible = None
            if kw.get("cible_model") and kw.get("cible_id"):
                try:
                    cible = {"model": kw["cible_model"], "id": int(kw["cible_id"])}
                except (TypeError, ValueError):
                    raise UserError(_("Fiche cible invalide."))
            return _rendre(self._page().document_deposer(
                image_b64=image_b64, filename=nom, titre=kw.get("titre"),
                destination=kw.get("destination") or "tampon", cible=cible,
                rappel=kw.get("rappel") or "aucun"))

        return _guarded(run)

    @http.route(f"{BASE}/facture", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def facture(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            image_b64, nom = _image()
            return _rendre(self._page().facture_deposer(image_b64=image_b64, filename=nom))

        return _guarded(run)

    @http.route(f"{BASE}/carte/lire", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def carte_lire(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            image_b64, nom = _image()
            return _rendre(self._page().scan_extract(image_b64=image_b64, filename=nom))

        return _guarded(run)

    @http.route(f"{BASE}/carte/enregistrer", type="http", auth="public",
                methods=["POST"], csrf=False, save_session=False)
    def carte_enregistrer(self, **kw):
        if not _authed():
            return _json({"error": "unauthorized"}, 401)

        def run():
            corps = _corps()
            return _rendre(self._page().scan_save(
                wizard_id=corps.get("wizard_id"), fields=corps.get("fields"),
                mode=corps.get("mode")))

        return _guarded(run)
