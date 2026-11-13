"""Les appels à l'API d'Expedibox, et rien d'autre : un geste par fonction.

L'hôte est figé (``https://api.expedibox.com``) : aucun champ, aucun réglage ne
le change. La session refuse aussi l'environnement (``trust_env = False``) : ni
mandataire, ni ``~/.netrc``, ni paquet d'autorités pris dans les variables ne
peuvent dérouter l'appel. Le jeton d'application de l'organisation voyage dans
l'en-tête ``X-App-Token``.

Ce que l'essai sur le casier de test a appris :

* Cloudflare refuse l'agent par défaut de ``requests`` (erreur 1010) : un
  User-Agent explicite est obligatoire ;
* sans courriel ni numéro de téléphone, la création d'un destinataire est
  refusée (400) ;
* la suppression rend 204, puis la relecture du même destinataire rend 404.
"""
import logging
from urllib.parse import quote

import requests

_logger = logging.getLogger(__name__)

API_ROOT = "https://api.expedibox.com"
# 🔴 Obligatoire : sans lui, Cloudflare rend une page d'erreur 1010 et aucun
# appel n'atteint Expedibox.
USER_AGENT = "Symbifox/1.0 (+https://symbifox.com)"
TIMEOUT = (10, 20)  # (connexion, lecture), en secondes
# ⚠️ `GET /recipients/` n'a AUCUN paramètre (spécification OpenAPI) : il rend les
# destinataires de TOUS les casiers du jeton, c'est-à-dire de toute
# l'organisation, et le filtrage par casier se fait ici. Un portefeuille de
# quarante immeubles tient dans ce plafond ; au-delà, l'ordre revient au
# responsable au lieu d'analyser n'importe quoi. Le corps est de toute façon
# déjà téléchargé par `requests` : ce plafond protège la lecture, pas la mémoire.
MAX_BYTES = 8 * 1024 * 1024
# Au-delà, le message de l'appareil est tronqué : il va dans un champ lu par
# une personne, pas dans un journal machine.
MAX_MESSAGE = 300
# Le bouton « Casiers du jeton » : assez de pages pour un gros portefeuille.
PAGE = 200
MAX_PAGES = 10

# Rejouer n'y changerait rien : la cause est dans la requête ou dans le compte.
PERMANENT_STATUSES = (400, 401, 403, 405, 409, 415, 422)


class ExpediboxError(Exception):
    """Un appel qui n'a pas abouti.

    ``permanent`` : la même requête échouerait pareil (données refusées, jeton
    invalide). Sinon, l'ordre se rejoue (panne, délai, 5xx, 429).
    """

    def __init__(self, message, status=None, permanent=False):
        super().__init__(message)
        self.status = status
        self.permanent = permanent


def _short(text):
    text = " ".join((text or "").split())
    return text[:MAX_MESSAGE] + "…" if len(text) > MAX_MESSAGE else text


def _session():
    session = requests.Session()
    # 🔴 Sans ça, `requests` honore HTTPS_PROXY, NO_PROXY, REQUESTS_CA_BUNDLE et
    # ~/.netrc : l'environnement du conteneur pourrait dérouter l'appel.
    session.trust_env = False
    return session


def call(method, path, token, payload=None, params=None):
    """Appelle Expedibox. Rend (code, données) ; lève ExpediboxError.

    Le 404 est rendu, jamais levé : l'appelant décide s'il est une bonne
    nouvelle (le destinataire à retirer n'est déjà plus là) ou non.
    """
    url = API_ROOT + path
    headers = {"X-App-Token": token, "User-Agent": USER_AGENT,
               "Accept": "application/json"}
    try:
        with _session() as session:
            response = session.request(
                method, url, headers=headers, json=payload, params=params,
                timeout=TIMEOUT, allow_redirects=False,
            )
    except requests.RequestException as exc:
        # Jamais le message de l'exception en entier : on garde le type, court
        # et net, et surtout rien du corps ni des en-têtes.
        raise ExpediboxError(
            "%s %s : %s" % (method, path, type(exc).__name__), permanent=False
        ) from exc
    status = response.status_code
    body = response.content or b""
    if len(body) > MAX_BYTES:
        raise ExpediboxError(
            "%s %s : réponse de %d octets, ignorée" % (method, path, len(body)),
            status=status, permanent=True,
        )
    data = None
    if body:
        try:
            data = response.json()
        except ValueError:
            data = None
    if 300 <= status < 400:
        # L'API ne redirige pas : une redirection veut dire qu'on ne parle pas
        # à l'API (portail, page d'erreur du pare-feu).
        raise ExpediboxError(
            "%s %s : redirection %d" % (method, path, status), status=status, permanent=True
        )
    if status in PERMANENT_STATUSES or status in (404, 410):
        return status, data
    if status >= 400:
        raise ExpediboxError(
            "%s %s : %d %s" % (method, path, status, _short(
                response.text if data is None else str(data))),
            status=status, permanent=False,
        )
    return status, data


def _ref(recipient_id):
    """Une référence vient d'un champ qu'une personne peut écrire : on la cite
    dans l'adresse sans jamais la laisser la changer (« ../ », « / »)."""
    return quote(str(recipient_id or ""), safe="")


def list_recipients(token, locker_id):
    """Les destinataires de CE casier. Rend une liste, jamais None."""
    status, data = call("GET", "/recipients/", token)
    if status != 200 or not isinstance(data, list):
        raise ExpediboxError(
            "GET /recipients/ : %d, réponse inattendue" % status,
            status=status, permanent=status in PERMANENT_STATUSES,
        )
    return [r for r in data if isinstance(r, dict) and r.get("id")
            and str(r.get("locker_id") or "") == str(locker_id)]


def find_recipient(token, locker_id, identifier):
    """Le destinataire de ce casier qui porte CET identifiant, ou None.

    🔴 La comparaison est exacte : c'est elle qui fait que le module ne
    reconnaît, et donc ne touche, que ses propres inscriptions.
    """
    if not identifier:
        return None
    for recipient in list_recipients(token, locker_id):
        if (recipient.get("custom_identifier") or "") == identifier:
            return recipient
    return None


def find_ours(token, locker_id, prefix):
    """Tous les destinataires de ce casier inscrits par CETTE base."""
    if not prefix:
        return []
    return [r for r in list_recipients(token, locker_id)
            if (r.get("custom_identifier") or "").startswith(prefix)]


def create_recipient(token, payload):
    status, data = call("POST", "/recipients/", token, payload=payload)
    if status == 201 and isinstance(data, dict) and data.get("id"):
        rendu = str(data.get("locker_id") or "")
        demande = str(payload.get("locker_id") or "")
        if rendu and rendu != demande:
            # Rattaché ailleurs que demandé : on le saurait sinon seulement au
            # départ, quand la garde refuserait le retrait.
            raise ExpediboxError(
                "POST /recipients/ : destinataire %s créé sur le casier %s, pas %s"
                % (data["id"], rendu, demande), status=status, permanent=True,
            )
        return str(data["id"])
    raise ExpediboxError(
        "POST /recipients/ : %d %s" % (status, _short(str(data))),
        status=status, permanent=status in PERMANENT_STATUSES,
    )


def read_recipient(token, recipient_id):
    """Le destinataire, ou None s'il n'est plus là."""
    status, data = call("GET", "/recipients/%s/" % _ref(recipient_id), token)
    if status == 200 and isinstance(data, dict):
        return data
    if status in (404, 410):
        return None
    raise ExpediboxError(
        "GET /recipients/%s/ : %d" % (recipient_id, status),
        status=status, permanent=status in PERMANENT_STATUSES,
    )


def patch_recipient(token, recipient_id, payload):
    """Corrige un destinataire déjà inscrit (nom, logement)."""
    status, data = call("PATCH", "/recipients/%s/" % _ref(recipient_id), token,
                        payload=payload)
    if status in (200, 202) and isinstance(data, dict):
        return data
    raise ExpediboxError(
        "PATCH /recipients/%s/ : %d %s" % (recipient_id, status, _short(str(data))),
        status=status, permanent=status in PERMANENT_STATUSES,
    )


def delete_recipient(token, recipient_id):
    """Rend le code HTTP du retrait ; 404 et 410 disent « déjà parti ».

    202 n'est pas accepté : « accepté » n'est pas « fait », et l'ordre se
    rejoue jusqu'à une réponse qui conclut.
    """
    status, data = call("DELETE", "/recipients/%s/" % _ref(recipient_id), token)
    if status in (200, 204, 404, 410):
        return status
    raise ExpediboxError(
        "DELETE /recipients/%s/ : %d %s" % (recipient_id, status, _short(str(data))),
        status=status, permanent=status in PERMANENT_STATUSES,
    )


def list_lockers(token):
    """Les casiers que ce jeton voit : pour le bouton d'essai du connecteur.

    L'enveloppe est paginée (``results``, ``count``) : on suit les pages,
    sinon un casier de la deuxième page serait introuvable et le gestionnaire
    recopierait un identifiant au hasard.
    """
    lockers, offset = [], 0
    for _page in range(MAX_PAGES):
        status, data = call("GET", "/lockers/", token,
                            params={"limit": PAGE, "offset": offset})
        if status != 200 or not isinstance(data, dict):
            raise ExpediboxError(
                "GET /lockers/ : %d" % status, status=status,
                permanent=status in PERMANENT_STATUSES,
            )
        page = [r for r in (data.get("results") or []) if isinstance(r, dict)]
        lockers.extend(page)
        offset += len(page)
        if not page or not data.get("next") or offset >= (data.get("count") or 0):
            break
    return lockers
