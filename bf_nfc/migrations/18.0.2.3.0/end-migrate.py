"""18.0.2.3.0 : ``bf_nfc.modeles_cibles`` (texte de noms techniques) devient des lignes.

Joué en FIN de montée, quand tous les modules installés sont chargés (cf. la note du
post-migrate). L'ancien paramètre n'est effacé que si CHAQUE nom a trouvé son modèle :
sinon il reste, réduit aux noms introuvables, et la liste se complète à la main.
"""
from odoo import SUPERUSER_ID, api

from odoo.addons.bf_nfc.hooks import TYPES_DEFAUT, semer_les_types


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()
    # ⚠️ `get_param` rend False quand le paramètre n'existe pas, jamais None : un
    # test `is None` ne se déclenche jamais, et la migration reposait alors le
    # paramètre sur une base qui ne l'avait jamais eu.
    liste = icp.get_param("bf_nfc.modeles_cibles") or ""
    noms = [n.strip() for n in liste.split(",") if n.strip()] if liste else TYPES_DEFAUT
    semer_les_types(env, noms)
    if not liste:
        return
    introuvables = [n for n in noms if n not in env]
    if introuvables:
        icp.set_param("bf_nfc.modeles_cibles", ",".join(introuvables))
    else:
        icp.search([("key", "=", "bf_nfc.modeles_cibles")]).unlink()
