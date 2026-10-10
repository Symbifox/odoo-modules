"""18.0.1.1.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_cx_onboarding"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.1.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_cadence', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_cadence', 'description'): '[action:bf_cx.action_cx_settings] '
                                                                   'Réglez le garde-fou '
                                                                   'anti-sursollicitation global '
                                                                   'et, au besoin, la cadence '
                                                                   'minimale propre à chaque '
                                                                   'programme (pratique : 90 jours '
                                                                   'pour un NPS relationnel).',
 ('data/bf_onboarding.xml', 'bf_onb_step_cadence', 'done_text'): 'Cadence configurée',
 ('data/bf_onboarding.xml', 'bf_onb_step_cadence', 'title'): 'Configurer la cadence',
 ('data/bf_onboarding.xml', 'bf_onb_step_complaint_team', 'button_text'): 'Ouvrir Utilisateurs',
 ('data/bf_onboarding.xml', 'bf_onb_step_complaint_team', 'description'): '[action:base.action_res_users] '
                                                                          'Ajoutez vos collègues '
                                                                          'aux groupes Expérience '
                                                                          'client (utilisateur ou '
                                                                          'gestionnaire) et réglez '
                                                                          "le délai d'accusé de "
                                                                          'réception des plaintes '
                                                                          'dans les paramètres.',
 ('data/bf_onboarding.xml', 'bf_onb_step_complaint_team', 'done_text'): 'Équipe Plaintes '
                                                                        'configurée',
 ('data/bf_onboarding.xml', 'bf_onb_step_complaint_team', 'title'): "Configurer l'équipe Plaintes",
 ('data/bf_onboarding.xml', 'bf_onb_step_program', 'button_text'): 'Ouvrir Programmes',
 ('data/bf_onboarding.xml', 'bf_onb_step_program', 'description'): '[action:bf_cx.action_cx_program] '
                                                                   'Créez un programme de mesure '
                                                                   '(NPS relationnel, CSAT '
                                                                   'transactionnel ou pulse '
                                                                   'continu) relié à son sondage '
                                                                   'Odoo.',
 ('data/bf_onboarding.xml', 'bf_onb_step_program', 'done_text'): 'Programme créé',
 ('data/bf_onboarding.xml', 'bf_onb_step_program', 'title'): 'Créer un programme',
 ('data/bf_onboarding.xml', 'bf_onb_step_wave', 'button_text'): 'Ouvrir Vagues',
 ('data/bf_onboarding.xml', 'bf_onb_step_wave', 'description'): '[action:bf_cx.action_cx_wave] '
                                                                'Composez une vague de '
                                                                'destinataires puis envoyez les '
                                                                'invitations : les réponses '
                                                                'alimentent automatiquement le '
                                                                'registre des feedbacks.',
 ('data/bf_onboarding.xml', 'bf_onb_step_wave', 'done_text'): 'Première vague envoyée',
 ('data/bf_onboarding.xml', 'bf_onb_step_wave', 'title'): 'Lancer une première vague',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Expérience client'}
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
        rec_en.write({champ: noeud.text.strip()})
