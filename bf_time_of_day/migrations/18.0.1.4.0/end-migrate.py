"""18.0.1.4.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_time_of_day"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.4.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): 'Définissez les périodes '
                                                                    '(matin, après-midi, soir) '
                                                                    'utilisées pour le filtrage et '
                                                                    'le routage des tâches.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): 'Plages définies',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): 'Configurer les plages horaires',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Plages horaires BF',
 ('data/bf_time_of_day_data.xml', 'tod_after_hours', 'name'): 'Hors heures',
 ('data/bf_time_of_day_data.xml', 'tod_eod', 'name'): 'Fin de jour',
 ('data/bf_time_of_day_data.xml', 'tod_midday', 'name'): 'Midi',
 ('data/bf_time_of_day_data.xml', 'tod_morning', 'name'): 'Matinée'}


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
