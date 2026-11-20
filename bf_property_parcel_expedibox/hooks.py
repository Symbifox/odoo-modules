"""Ce que ce module exige du voisin qu'il ne peut pas déclarer en dépendance.

🔴 « Un seul avis au résident » repose ENTIÈREMENT sur une clé de contexte
(`bf_parcel_notice_elsewhere`), et cette clé n'est honorée que par
``bf_property_sms`` 18.0.2.3.0. Devant une version plus ancienne, le casier
écrit au destinataire ET le texto part : deux avis par colis, facturés, sans
une ligne au fil pour le dire (relecture adverse du 2026-11-19, 2e tour — le
module jumeau avait cette garde, pas celui-ci).

``bf_property_sms`` n'est pas une dépendance : un immeuble sans texto n'a pas à
l'installer. S'il est là, il doit porter la garde.
"""
from odoo import _
from odoo.exceptions import UserError
from odoo.modules.module import get_manifest
from odoo.tools import parse_version

VOISIN = "bf_property_sms"
EXIGE = "18.0.2.3.0"


def version_suffisante(version):
    """Vrai si cette version du voisin porte la garde. Pure, donc éprouvable."""
    try:
        return parse_version(version or "") >= parse_version(EXIGE)
    except Exception:  # noqa: BLE001 — une version illisible n'est pas une garde
        return False


def post_init_hook(env):
    voisin = env["ir.module.module"].sudo().search([("name", "=", VOISIN)], limit=1)
    if not voisin or voisin.state not in ("installed", "to upgrade", "to install"):
        return
    # La version du DISQUE : les deux modules montent dans la même fenêtre, et
    # l'ordre de chargement ne garantit pas que le voisin soit déjà monté.
    pose = (get_manifest(VOISIN) or {}).get("version") or voisin.latest_version or ""
    if version_suffisante(pose):
        return
    raise UserError(_(
        "%(voisin)s est en %(pose)s, et les avis de colis exigent %(exige)s : "
        "sans elle, le casier avise le résident ET un texto part, pour le même "
        "colis. Montez %(voisin)s dans la même fenêtre, puis reprenez cette "
        "installation.")
        % {"voisin": VOISIN, "pose": pose or _("version inconnue"), "exige": EXIGE})
