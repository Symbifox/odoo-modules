"""18.0.2.4.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

Le catalogue n'est PAS rechargé en écrasant : le chargement ordinaire ajoute le
fr_CA là où il manque, et un rechargement forcé effacerait une traduction
retouchée par le locataire (aucun catalogue fautif à corriger dans ce module).

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

MODULE = "bf_mail_vigie"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.4.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_settings', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'description'): 'Définissez les seuils '
                                                                    "d'inactivité et les "
                                                                    "destinataires d'alertes pour "
                                                                    'les boîtes surveillées.',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'done_text'): 'Vigie active',
 ('data/bf_onboarding.xml', 'bf_onb_step_settings', 'title'): 'Configurer la vigie courriel',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'BF Mail Vigie'}
# Champs devenus traduisibles dans cette version (voir la note sur les retouches).
CONVERTIS = []


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
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
        rec_en.write({champ: noeud.text.strip()})
