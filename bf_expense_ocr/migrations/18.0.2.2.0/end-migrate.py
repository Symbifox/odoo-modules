"""18.0.2.2.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_expense_ocr"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.2.0
LIVRES_FR = {('data/ocr_cron.xml', 'ir_cron_expense_ocr_batch', 'name'): 'Lecture des reçus : rattrapage'}
# Champs devenus traduisibles dans cette version (voir la note sur les retouches).
CONVERTIS = []


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        if not rec or noeud is None or not (noeud.text or "").strip():
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() != francais:
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            if (rec._name, champ) in CONVERTIS:
                # 🔴 La colonne vient d'être convertie : le catalogue a ajouté le
                # français LIVRÉ en fr_CA, qui masquerait la retouche. On la recopie.
                rec.update_field_translations(champ, {lang: rec_en[champ] for lang in langues})
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait échouer toute la montée si le travail tourne à ce moment.
        cible = rec_en.ir_actions_server_id if rec._name == "ir.cron" else rec_en
        cible.write({champ: noeud.text.strip()})
