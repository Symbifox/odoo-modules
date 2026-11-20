"""Les deux lectures dont l'avis a besoin, et rien d'autre.

Le client d'API vit dans ``bf_property_access_expedibox`` (hôte figé, session
qui refuse l'environnement, User-Agent explicite, délais bornés, aucune
redirection suivie, 404 rendu et jamais levé) : il n'est pas réécrit ici, il
est appelé.

⚠️ ``include_qr_code_src=false`` sur chaque lecture : sans lui, la réponse
embarque trois images QR en base64 — les codes qui ouvrent la porte du casier.
On ne veut pas les voir passer, même pour les jeter.
"""
from urllib.parse import quote

from odoo.addons.bf_property_access_expedibox.models import expedibox_api as api

# Ce qu'une réservation nous apprend. Tout le reste de leur objet — les codes
# de dépôt et de retrait, les QR, les photos, le code d'ouverture de la porte —
# n'entre jamais dans cette base.
KEPT = (
    "id", "locker_id", "recipient_id", "recipient_custom_identifier",
    # 🔴 Ces quatre-là ne sont pas du décor : ce sont eux qui disent ce qui
    # s'est passé (voir `_confirmed`). Le mot reçu dans un corps non signé ne
    # décide de rien.
    "deposit_at", "pickup_at", "end_at", "deposit_expired",
    "notify_receiver", "carrier", "tracking_number", "unit", "door", "timezone",
)


def _kept(data):
    """Ne garde que les champs utiles, et la position de la porte."""
    if not isinstance(data, dict):
        return None
    kept = {k: data.get(k) for k in KEPT if k in data}
    door = kept.get("door")
    # ⛔ `door` porte aussi `unlock_code` : seule la position est reprise.
    if isinstance(door, dict):
        kept["door"] = door.get("position")
    elif isinstance(door, (str, int)):
        # Leur spécification dit un objet, leur API pourrait rendre la position
        # telle quelle : « A3 » reste « A3 », jamais « inconnue ».
        kept["door"] = str(door)
    else:
        kept["door"] = None
    return kept


def get_reservation(token, reference):
    """Rend (code, réservation élaguée). 404 rendu, jamais levé."""
    status, data = api.call(
        "GET", "/reservations/%s/" % quote(str(reference or ""), safe=""), token,
        params={"include_qr_code_src": "false"},
    )
    return status, _kept(data)


def get_recipient(token, reference):
    """Rend (code, identifiant de rapprochement) — pas la fiche.

    ⚠️ La fiche d'un destinataire porte son courriel et son mobile. Ici, seul
    ``custom_identifier`` sort de la fonction : c'est tout ce que le
    rapprochement demande, et rien de personnel n'a à voyager plus loin.
    """
    status, data = api.call(
        "GET", "/recipients/%s/" % quote(str(reference or ""), safe=""), token)
    if not isinstance(data, dict):
        return status, None
    return status, data.get("custom_identifier") or None
