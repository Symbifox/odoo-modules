"""18.0.1.1.0 : la case du geste change de libellé.

« Also change the link page address » devient « Also change the addresses that
carry the name » : elle couvre maintenant le dépôt sécurisé et les rendez-vous.
🔴 Une mise à jour d'Odoo n'écrase JAMAIS une traduction déjà en base : la
case garderait en français l'ancien libellé, « Changer aussi l'adresse de la
page de liens », qui ne dit plus ce qu'elle fait. On recharge donc
le catalogue de ce module en écrasant.

⚠️ En `end` et non en `post` : Odoo charge les traductions du module juste
après le `post`, sans écraser, et c'est ce passage qui doit venir en dernier.
"""

from odoo import SUPERUSER_ID, api

MODULE = "bf_preferred_name_linkpage"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    # La clé de la réserve des adresses retirées, créée ici et non au premier
    # appel d'un usager.
    env["bf.linkpage.retired.slug"]._key()
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
