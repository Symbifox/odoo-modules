"""UPS : jeton de compte, puis suivi d'un numéro.

Lu à la source le 2026-11-19 dans les spécifications OpenAPI que UPS publie
(``github.com/UPS-API/api-documentation``, fichiers ``Tracking.yaml`` et
``OAuthClientCredentials.yaml``) : le portail ``developer.ups.com`` refuse tout
client qui n'est pas un navigateur, mais ses spécifications, elles, sont
publiques.

* jeton : ``POST /security/v1/oauth/token``, **Basic** (identifiant client en
  usager, secret en mot de passe), corps ``grant_type=client_credentials``,
  en-tête facultatif ``x-merchant-id`` (« 6-digit UPS account number »).
  Réponse : ``access_token``, ``expires_in`` (secondes, **en chaîne**) ;
* suivi : ``GET /api/track/v1/details/{inquiryNumber}`` (la spécification écrit
  le chemin sans ``/api``, que l'hôte porte déjà : ``…ups.com/api``), en-têtes **obligatoires**
  ``transId`` et ``transactionSrc``, numéro de 7 à 34 caractères ;
* hôtes : ``wwwcie.ups.com`` (essai) et ``onlinetools.ups.com`` (production).

🔴 **Ce qui conclut « livré », et seulement lui** : une entrée de
``deliveryDate`` de type ``DEL``, documentée dans la spécification (« DEL -
Delivered Date »). Le vocabulaire de ``currentStatus.type`` et de
``statusCode``, lui, vit dans l'« Appendix » du portail, que personne ici n'a
pu lire : il est donc gardé tel quel pour l'opérateur et affiché dans les mots
d'UPS, mais il ne décide de rien. Conclure sur une liste devinée, c'est
annoncer un colis livré qui ne l'est pas.
"""
import logging
import uuid
from datetime import datetime
from urllib.parse import quote

import pytz

from . import carrier_api as api

_logger = logging.getLogger(__name__)

HOSTS = {
    "test": "https://wwwcie.ups.com",
    "production": "https://onlinetools.ups.com",
}
TOKEN_PATH = "/security/v1/oauth/token"
TRACK_PATH = "/api/track/v1/details/%s"
# La source de l'appel, telle qu'UPS la demande : elle paraît dans leurs
# journaux et dans leurs quotas.
TRANSACTION_SRC = "Symbifox"
# Le fuseau du dossier, à défaut de celui que le transporteur ne donne pas.
DEFAULT_TZ = "America/Toronto"
# Spécification : « Each inquiry number must be between 7 and 34 characters ».
MIN_NUMBER, MAX_NUMBER = 7, 34


def host(account):
    return HOSTS.get(account.environment, HOSTS["production"])


def fetch_token(account):
    """Rend (jeton, secondes de validité). Lève ``CarrierError``."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if account.ups_account_number:
        headers["x-merchant-id"] = account.ups_account_number
    status, data = api.call(
        "POST", host(account) + TOKEN_PATH, headers=headers,
        data={"grant_type": "client_credentials"},
        auth=(account.ups_client_id or "", account.ups_client_secret or ""),
    )
    if status == 403:
        raise api.CarrierError(
            "UPS refuse ce compte (« Blocked Merchant »)", status=status,
            permanent=True)
    if status == 429:
        raise api.CarrierError("UPS : quota de jetons atteint", status=status,
                               permanent=False)
    if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
        raise api.CarrierError(
            "UPS n'a pas délivré de jeton (code %s)" % status, status=status,
            permanent=status in api.PERMANENT_STATUSES)
    # ⚠️ `expires_in` est une CHAÎNE dans leur spécification : un int() direct
    # sur une valeur absente ou vide ferait tomber l'appel au lieu de rendre un
    # jeton parfaitement utilisable.
    try:
        lifetime = int(str(data.get("expires_in") or "0").strip())
    except ValueError:
        lifetime = 0
    return data["access_token"], max(lifetime, 0)


def _instant(date_text, time_text, tz_name=DEFAULT_TZ):
    """``AAAAMMJJ`` + ``HHMMSS`` d'UPS vers un datetime naïf UTC, ou None.

    🔴 Ces deux champs sont donnés **à l'heure locale du lieu de la
    livraison** ; Odoo, lui, range de l'UTC. Sans conversion, une livraison de
    18 h 15 à Montréal s'affichait « 13 h 15 » au portail de la résidente, et
    une livraison sans heure (minuit) changeait carrément de jour (relecture
    adverse du 2026-11-19). À défaut du fuseau du lieu, qu'UPS ne donne pas
    ici, on prend celui du dossier : c'est le fuseau où le colis est attendu.
    """
    if not isinstance(date_text, str) or len(date_text.strip()) != 8:
        return None
    text = date_text.strip() + (time_text or "120000").strip().ljust(6, "0")[:6]
    try:
        local = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    try:
        zone = pytz.timezone(tz_name or DEFAULT_TZ)
    except pytz.UnknownTimeZoneError:
        zone = pytz.timezone(DEFAULT_TZ)
    return zone.localize(local).astimezone(pytz.utc).replace(tzinfo=None)


def track(account, number, tz_name=DEFAULT_TZ):
    """Rend un constat normalisé : (état, libellé, instant, code brut)."""
    number = (number or "").strip()
    if not MIN_NUMBER <= len(number) <= MAX_NUMBER:
        raise api.CarrierError(
            "un numéro UPS compte de 7 à 34 caractères", permanent=True)
    token = account._ups_token()
    headers = {
        "Authorization": "Bearer %s" % token,
        # Spécification : « An identifier unique to the request ».
        "transId": uuid.uuid4().hex,
        "transactionSrc": TRANSACTION_SRC,
    }
    status, data = api.call(
        "GET", host(account) + TRACK_PATH % quote(number, safe=""),
        headers=headers, params={"locale": account._ups_locale()},
    )
    if status == 404:
        return ("not_found", None, None, "404")
    if status == 401:
        # Le jeton a pu expirer avant son heure. Il est jeté par l'appelant,
        # hors du point de sauvegarde (voir CarrierError.token_invalid).
        raise api.CarrierError("UPS a refusé le jeton", status=status,
                               permanent=False, token_invalid=True)
    if status == 403:
        raise api.CarrierError("UPS refuse ce compte (« Blocked Merchant »)",
                               status=status, permanent=True)
    if status == 429:
        raise api.CarrierError("UPS : quota atteint", status=status,
                               permanent=False)
    if status != 200 or not isinstance(data, dict):
        raise api.CarrierError("UPS : réponse inattendue (code %s)" % status,
                               status=status,
                               permanent=status in api.PERMANENT_STATUSES)
    return _read(data, tz_name)


def _read(data, tz_name=DEFAULT_TZ):
    shipments = ((data.get("trackResponse") or {}).get("shipment") or [])
    packages = []
    for shipment in shipments:
        if isinstance(shipment, dict):
            packages += [p for p in (shipment.get("package") or [])
                         if isinstance(p, dict)]
    if not packages:
        return ("unknown", None, None, None)
    package = packages[0]
    current = package.get("currentStatus") or {}
    label = current.get("description") or current.get("simplifiedTextDescription")
    raw = "/".join(str(current.get(k)) for k in ("type", "statusCode")
                   if current.get(k))
    dates = [d for d in (package.get("deliveryDate") or []) if isinstance(d, dict)]
    delivered = next((d for d in dates if d.get("type") == "DEL"), None)
    if delivered:
        times = package.get("deliveryTime") or {}
        end = times.get("endTime") if times.get("type") == "DEL" else None
        return ("delivered", label,
                _instant(delivered.get("date"), end, tz_name), raw)
    # Une date prévue ou replanifiée dit que le colis est pris en charge, sans
    # prétendre savoir où il est.
    if any(d.get("type") in ("SDD", "RDD") for d in dates):
        return ("in_transit", label, None, raw)
    return ("unknown", label, None, raw)
