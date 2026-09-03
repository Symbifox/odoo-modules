"""Garder la découverte WOPI en mémoire au lieu de la retélécharger.

Le connecteur amont appelle ``discover.collabora_url`` à CHAQUE ouverture de
document : une requête HTTP synchrone vers le serveur Collabora avant même que
l'iframe s'affiche. Le fichier ne change qu'à une mise à niveau du serveur.

On remplace la fonction dans le module amont plutôt que d'en recopier le
contrôleur : le contrôleur fait ``discover.collabora_url(...)``, donc la
résolution de l'attribut a lieu à l'appel, et le remplacement suffit. Le code
amont reste intact et continue de recevoir ses mises à jour.

⚠️ Le cache porte une DURÉE DE VIE, pas un vidage manuel. L'adresse rendue par
la découverte contient le numéro de compilation de Collabora
(``/browser/<hash>/cool.html``) : gardée indéfiniment, elle pointerait vers un
chemin disparu au premier ``update-collabora.sh``. La fenêtre d'erreur est
bornée par le TTL, et un vidage explicite existe pour ne pas l'attendre.
"""

import logging
import threading
import time

from odoo.addons.collabora_odoo.utils import discover

_logger = logging.getLogger(__name__)

TTL_DEFAUT = 900  # secondes

_verrou = threading.Lock()
_cache = {}  # (serveur, type MIME) -> (péremption monotone, urlsrc)
_original = discover.collabora_url


def _duree_de_vie():
    """Lire le réglage sans exiger une requête : un cron n'en a pas."""
    try:
        from odoo.http import request
        valeur = request.env["ir.config_parameter"].sudo().get_param(
            "bf_collabora.decouverte_ttl")
    except Exception:
        return TTL_DEFAUT
    if valeur in (None, False, ""):
        return TTL_DEFAUT
    try:
        return max(0, int(valeur))
    except (TypeError, ValueError):
        return TTL_DEFAUT


def vider():
    """Oublier tout ce qui est gardé. Appelé après une mise à niveau du serveur."""
    with _verrou:
        nombre = len(_cache)
        _cache.clear()
    _logger.info("Cache de découverte Collabora vidé (%s entrée(s))", nombre)
    return nombre


def contenu():
    """Pour les essais et le diagnostic : ce que le cache tient en ce moment."""
    with _verrou:
        return dict(_cache)


def collabora_url(server, mime_type, disable_verify_cert=False):
    ttl = _duree_de_vie()
    if not ttl:
        return _original(server, mime_type, disable_verify_cert)

    cle = (server, mime_type)
    maintenant = time.monotonic()
    with _verrou:
        entree = _cache.get(cle)
        if entree and entree[0] > maintenant:
            return entree[1]

    # Hors verrou : un appel réseau ne doit pas bloquer les autres travailleurs.
    # Deux requêtes simultanées sur un cache froid font deux appels, ce qui est
    # moins cher qu'une file d'attente derrière un verrou tenu pendant un aller
    # retour HTTP.
    url = _original(server, mime_type, disable_verify_cert)
    with _verrou:
        _cache[cle] = (time.monotonic() + ttl, url)
    return url


discover.collabora_url = collabora_url
