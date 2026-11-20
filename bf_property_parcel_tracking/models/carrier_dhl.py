"""DHL : une clé dans un en-tête, un numéro dans une requête.

Mesuré à la source le 2026-11-19, **sur l'API elle-même** : le portail
``developer.dhl.com`` rend une page « Request Rejected » à tout client qui
n'est pas un navigateur (ici comme depuis un autre réseau), mais l'API répond.

    GET https://api-eu.dhl.com/track/shipments?trackingNumber=…   → 401
    content-type: application/problem+json
    {"status":401,"title":"Unauthorized","detail":"Access to the resource is not allowed."}
    access-control-allow-headers: … DHL-API-Key …

De cette réponse, trois faits tenus pour sûrs : l'hôte et le chemin, le nom de
l'en-tête de clé (**``DHL-API-Key``**, nommé par le serveur), et la forme des
erreurs (RFC 7807 : ``status``, ``title``, ``detail``). Leurs en-têtes
``correlation-id`` et ``x-request-id`` sont consignés quand un appel échoue :
c'est ce que leur soutien demande.

🔴 **Ce qui n'a PAS été lu à la source** : le vocabulaire de
``shipments[].status.statusCode``. Il est donc traité comme une liste
**ouverte** : seul ``delivered``, exactement, conclut à une livraison ; toute
autre valeur — connue ou inconnue — laisse le colis en « on ne sait pas », avec
le texte de DHL affiché tel quel et le code brut gardé pour l'opérateur. Un
vocabulaire deviné aurait annoncé des colis livrés qui ne l'étaient pas.
"""
import logging
from datetime import datetime, timezone

from . import carrier_api as api

_logger = logging.getLogger(__name__)

URL = "https://api-eu.dhl.com/track/shipments"
# Ce qui conclut, et rien d'autre.
DELIVERED = "delivered"
# Ce qu'on reconnaît sans en conclure une livraison. Non lu à la source : la
# correspondance sert l'affichage, pas la décision.
KNOWN = {
    "pre-transit": "pre_transit",
    "transit": "in_transit",
    "out-for-delivery": "out_for_delivery",
    "failure": "exception",
    "unknown": "unknown",
}


def _instant(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(microsecond=0)
    return moment.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def track(account, number):
    number = (number or "").strip()
    if not number:
        raise api.CarrierError("aucun numéro à suivre", permanent=True)
    status, data = api.call(
        "GET", URL,
        headers={"DHL-API-Key": account.dhl_api_key or ""},
        params={"trackingNumber": number, "language": account._dhl_language()},
    )
    if status == 404:
        return ("not_found", None, None, "404")
    if status in (401, 403):
        raise api.CarrierError("DHL a refusé la clé (code %s)" % status,
                               status=status, permanent=True)
    if status == 429:
        raise api.CarrierError("DHL : quota atteint", status=status,
                               permanent=False)
    if status != 200 or not isinstance(data, dict):
        raise api.CarrierError("DHL : réponse inattendue (code %s)" % status,
                               status=status,
                               permanent=status in api.PERMANENT_STATUSES)
    return _read(data)


def _read(data):
    shipments = [s for s in (data.get("shipments") or []) if isinstance(s, dict)]
    if not shipments:
        return ("unknown", None, None, None)
    status = shipments[0].get("status") or {}
    raw = status.get("statusCode") or status.get("status")
    label = status.get("description") or status.get("status")
    instant = _instant(status.get("timestamp"))
    if isinstance(raw, str) and raw.strip().lower() == DELIVERED:
        return ("delivered", label, instant, raw)
    state = KNOWN.get((raw or "").strip().lower() if isinstance(raw, str) else "",
                      "unknown")
    return (state, label, instant, raw if isinstance(raw, str) else None)
