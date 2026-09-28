"""Les types de fiche qu'une étiquette QR vise le plus souvent.

⚠️ Semés seulement s'ils existent dans cette base, par le même outil que le
socle : un modèle absent ferait refuser la ligne, donc l'installation. Un module
installé plus tard (plans d'étage, événements) se rattrape par le bouton
« Ajouter les types suggérés » des réglages.
"""
from odoo.addons.bf_nfc.hooks import semer_les_types

TYPES_SUGGERES = [
    "bf.floorplan.element",
    "bf.floorplan.zone",
    "bf.floorplan",
    "hosting.endpoint",
    "hosting.server",
    "maintenance.equipment",
    "event.event",
    "bf.linkpage",
]


def post_init_hook(env):
    semer_les_types(env, TYPES_SUGGERES)
