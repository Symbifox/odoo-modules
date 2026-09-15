"""18.0.1.0.2 : « Endroit » reprend son nom, que la 1.0.1 avait laissé à « Élément vérifié ».

La 1.0.1 a scindé un champ en deux — l'élément vérifié d'un côté, l'endroit de
l'autre — mais les catalogues de traduction n'ont pas été réexportés. Le `.po`
continuait donc de nommer ``place`` « Élément vérifié », et l'écran des relevés
affichait DEUX colonnes du même nom : celle qui porte l'extincteur et celle qui
porte le mur où il est accroché.

🔴 Un simple ``-u`` ne suffit pas à le réparer. Le chargeur de traductions écrit
avec ``overwrite=False`` : un terme qui porte DÉJÀ une valeur est sauté, même
fausse. La valeur fautive survivrait donc à toutes les mises à jour suivantes.
Il faut l'effacer d'abord, puis redemander le chargement.

⚠️ En ``end-migrate`` et pas en ``post-migrate`` : les traductions du module se
chargent après le post-migrate, et elles écraseraient le travail fait ici.
"""
CHAMPS = ("place",)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE ir_model_fields
           SET field_description = field_description - 'fr_CA' - 'en_CA',
               help = CASE WHEN help IS NULL THEN NULL ELSE help - 'fr_CA' - 'en_CA' END
         WHERE model = 'bf.nfc.reading' AND name IN %s
    """, (CHAMPS,))
    if not cr.rowcount:
        return
    # Le rechargement se fait par l'ORM : lui seul sait où sont les .po du module
    # et quelles langues sont installées.
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [c for c, _n in env["res.lang"].get_installed()]
    env["ir.module.module"]._load_module_terms(["bf_nfc_inspection"], langues, overwrite=True)
