"""Ce que ce module exige du voisin qu'il ne peut pas déclarer en dépendance.

🔴 L'état « Annoncé » n'a de sens que si l'avis du journal attend la livraison.
Cette garde vit dans ``bf_property_sms`` 18.0.2.3.0 (« l'avis ne part que pour
un colis EN DÉPÔT »), et ``bf_property_sms`` n'est PAS une dépendance : un
immeuble sans texto n'a pas à l'installer.

Posé seul devant un ``bf_property_sms`` plus ancien, ce module ferait partir un
texto « un colis est arrivé » à chaque ANNONCE — un message faux, et payé
(relecture adverse du 2026-11-19). Plutôt que de le laisser arriver en silence,
l'installation refuse et dit quoi monter dans la même fenêtre.
"""
import logging

from odoo import _
from odoo.exceptions import UserError
from odoo.modules.module import get_manifest
from odoo.tools import parse_version

_logger = logging.getLogger(__name__)

VOISIN = "bf_property_sms"
EXIGE = "18.0.2.3.0"


def version_suffisante(version):
    """Vrai si cette version du voisin porte la garde. Pure, donc éprouvable."""
    try:
        return parse_version(version or "") >= parse_version(EXIGE)
    except Exception:  # noqa: BLE001 — une version illisible n'est pas une garde
        return False


def uninstall_hook(env):
    """Aucune fiche ne reste dans un état que la sélection ne connaît plus.

    🔴 `state` est redéfini par ce module (« Annoncé » devant les états du
    journal). Retiré, les colis restés en `expected` portent une valeur hors
    sélection sur un champ requis : formulaire vide et non enregistrable,
    jamais ramassés par la purge du socle (qui ne prend que « remis » et
    « retourné »), et un geste qui lit le libellé de l'état lève un KeyError.

    Une annonce n'est pas un colis reçu : elle s'efface, elle ne se convertit
    pas en « en dépôt » — le registre dirait que le syndicat détient un colis
    qu'il n'a jamais eu.
    """
    parcels = env["bf.property.parcel"].sudo().search([("state", "=", "expected")])
    if parcels:
        _logger.info("Retrait du suivi des colis : %d annonce(s) effacée(s)",
                     len(parcels))
        parcels.unlink()


def post_init_hook(env):
    voisin = env["ir.module.module"].sudo().search([("name", "=", VOISIN)], limit=1)
    if not voisin or voisin.state not in ("installed", "to upgrade", "to install"):
        return
    # 🔴 La version du DISQUE, pas celle de la base : les deux modules montent
    # dans la même fenêtre, et l'ordre de chargement met celui-ci AVANT le
    # voisin. Lue en base, la version serait encore l'ancienne et la pose
    # échouerait sur sa propre garde. Ce qui compte est le code qui va tourner.
    pose = (get_manifest(VOISIN) or {}).get("version") or voisin.latest_version or ""
    if version_suffisante(pose):
        return
    raise UserError(_(
        "%(voisin)s est en %(pose)s, et le suivi des colis exige %(exige)s : "
        "sans elle, un texto « votre colis est arrivé » partirait dès qu'un "
        "résident ANNONCE un colis encore en route. Montez %(voisin)s dans la "
        "même fenêtre, puis reprenez cette installation.")
        % {"voisin": VOISIN, "pose": pose or _("version inconnue"), "exige": EXIGE})
