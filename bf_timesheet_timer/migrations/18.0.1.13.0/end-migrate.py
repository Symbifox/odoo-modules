"""18.0.1.13.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_timesheet_timer"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.13.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_presets', 'button_text'): 'Voir les présets',
 ('data/bf_onboarding.xml', 'bf_onb_step_presets', 'description'): '[action:bf_timesheet_timer.action_bf_timer_preset] '
                                                                   'Préparez des préréglages '
                                                                   'projet/tâche pour démarrer un '
                                                                   'minuteur en un clic.',
 ('data/bf_onboarding.xml', 'bf_onb_step_presets', 'done_text'): 'Présets prêts',
 ('data/bf_onboarding.xml', 'bf_onb_step_presets', 'title'): 'Configurer les présets',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): "Choisissez l'arrondi, le "
                                                                    "seuil d'inactivité et le "
                                                                    'raccourci clavier du '
                                                                    'minuteur.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): 'Minuteur prêt',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): 'Régler le minuteur',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Minuteur de feuille de temps'}


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
