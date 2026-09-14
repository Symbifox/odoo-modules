"""18.0.3.3.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_bureau"
# (fichier, identifiant, champ) -> français livré avant 18.0.3.3.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_open_desk', 'button_text'): 'Ouvrir le bureau',
 ('data/bf_onboarding.xml', 'bf_onb_step_open_desk', 'description'): '[action:bf_bureau.bf_bureau_open_default_desk_action] '
                                                                     'Lancez votre vue bureau pour '
                                                                     'personnaliser les widgets.',
 ('data/bf_onboarding.xml', 'bf_onb_step_open_desk', 'done_text'): 'Bureau actif',
 ('data/bf_onboarding.xml', 'bf_onb_step_open_desk', 'title'): 'Ouvrir votre bureau',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): 'Choisissez les widgets à '
                                                                    'afficher et leur disposition '
                                                                    'par défaut.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): 'Préférences réglées',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): 'Configurer votre bureau',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Bureau Blue Fox'}


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
            continue
        rec_en.write({champ: noeud.text.strip()})
