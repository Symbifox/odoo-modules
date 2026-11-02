"""Typographie française : une espace avant le deux-points, « ex. » au lieu de « ex: »,
et des coquilles (« ID de fichier Nextcloud », « Archivé », « Connexion réussie. », nom du
travail planifié).

🔴 Une mise à jour n'écrase jamais une traduction déjà en base : on recharge le catalogue
du module en écrasant, pour les seules langues françaises installées.

🔴 L'écrasement respecte `noupdate` : le nom du travail planifié garde sa clé fr_CA. On la
remplace ici, seulement si elle porte encore un texte livré (un nom retouché à la main reste),
par son action : `ir.cron.write` prend un verrou NOWAIT qui ferait échouer la montée si le
travail tourne à ce moment.
"""

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL

MODULE = "bf_document_nextcloud_sync"
NOMS_LIVRES = ("Documents: Verifier modifications Nextcloud", "Documents: Vérifier modifications Nextcloud")
NOM = "Documents : vérifier les modifications Nextcloud"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code.startswith("fr")]
    if not langues:
        return
    env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    cron = env.ref(f"{MODULE}.ir_cron_check_nc_modifications", raise_if_not_found=False)
    if not cron:
        return
    action = cron.ir_actions_server_id
    for lang in langues:
        cr.execute(SQL(
            "UPDATE ir_act_server SET name = name || jsonb_build_object(%s, %s::text)"
            " WHERE id = %s AND name->>%s = ANY(%s)",
            lang, NOM, action.id, lang, list(NOMS_LIVRES)))
    action.invalidate_recordset(["name"])
    cron.invalidate_recordset()
