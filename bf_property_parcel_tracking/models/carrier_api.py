"""Le fil par lequel on parle aux transporteurs, et rien de plus.

Même durcissement que le client d'Expedibox, pour les mêmes raisons :

* la session **refuse l'environnement** (``trust_env = False``) : ni mandataire,
  ni ``~/.netrc``, ni paquet d'autorités pris dans les variables ne peuvent
  dérouter l'appel ;
* **User-Agent explicite** : un pare-feu d'intermédiaire refuse l'agent par
  défaut de ``requests`` (Cloudflare rend une erreur 1010 à Expedibox ; rien ne
  dit que UPS ou DHL soient plus tolérants) ;
* **délais bornés**, **aucune redirection suivie** (une redirection veut dire
  qu'on ne parle pas à l'API mais à un portail ou à une page d'erreur), et une
  **réponse plafonnée** ;
* ⛔ **jamais le corps de la réponse dans un journal** : le suivi d'un colis
  nomme l'adresse de livraison, parfois la personne qui a signé.
"""
import logging

import requests

_logger = logging.getLogger(__name__)

USER_AGENT = "Symbifox/1.0 (+https://symbifox.com)"
TIMEOUT = (10, 20)
MAX_BYTES = 2 * 1024 * 1024
MAX_MESSAGE = 300
# Rejouer n'y changerait rien : la cause est dans la requête ou dans le compte.
PERMANENT_STATUSES = (400, 401, 403, 405, 415, 422)


class CarrierError(Exception):
    """Un appel qui n'a pas abouti.

    ``permanent`` : la même requête échouerait pareil (clé refusée, numéro
    invalide). Sinon, l'interrogation se rejoue (panne, délai, 5xx, 429).
    """

    def __init__(self, message, status=None, permanent=False, token_invalid=False):
        super().__init__(message)
        self.status = status
        self.permanent = permanent
        # 🔴 Le jeton ne se jette pas ici : on est dans le point de sauvegarde
        # du colis, que l'exception va faire revenir en arrière — l'oubli
        # serait annulé avec le reste, et le colis suivant repartirait avec le
        # même jeton mort (mutant survivant du 2026-11-19). L'appelant le jette
        # APRÈS le retour en arrière.
        self.token_invalid = token_invalid


def short(text):
    text = " ".join((text or "").split())
    return text[:MAX_MESSAGE] + "…" if len(text) > MAX_MESSAGE else text


def session():
    http = requests.Session()
    http.trust_env = False
    return http


def call(method, url, headers=None, params=None, data=None, auth=None,
         expect_json=True):
    """Appelle un transporteur. Rend (code, données). Lève ``CarrierError``.

    Les 404 et 429 sont RENDUS, jamais levés : un numéro inconnu est une
    réponse, et un quota atteint se lit au niveau du compte.
    """
    sent_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    sent_headers.update(headers or {})
    try:
        with session() as http:
            response = http.request(
                method, url, headers=sent_headers, params=params, data=data,
                auth=auth, timeout=TIMEOUT, allow_redirects=False,
            )
    except requests.RequestException as exc:
        # Le type de l'exception, court et net : rien du corps, rien des
        # en-têtes, rien de l'adresse complète.
        raise CarrierError("%s : %s" % (method, type(exc).__name__),
                           permanent=False) from exc
    status = response.status_code
    body = response.content or b""
    if len(body) > MAX_BYTES:
        raise CarrierError("réponse de %d octets, ignorée" % len(body),
                           status=status, permanent=True)
    if 300 <= status < 400:
        raise CarrierError("redirection %d : ce n'est pas l'API" % status,
                           status=status, permanent=True)
    data = None
    if body and expect_json:
        try:
            data = response.json()
        except ValueError:
            data = None
    if status in (200, 201, 404, 429) or status in PERMANENT_STATUSES:
        return status, data
    raise CarrierError("code %d" % status, status=status, permanent=False)
