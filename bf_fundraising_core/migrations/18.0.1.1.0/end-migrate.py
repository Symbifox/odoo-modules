"""18.0.1.1.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait ce module en français.

🔴 Une mise à jour n'écrase jamais une traduction existante : le catalogue du
module est rechargé en écrasant (le catalogue d'avant ne traduisait rien, ou
recopiait le français).

Les données `noupdate` gardent leur valeur en_US française après la mise à jour.
On bascule chaque champ SEULEMENT s'il porte encore le texte livré; l'anglais est
lu dans le fichier de données du module. Une valeur retouchée est laissée.

🔴 Une retouche qui n'a pas de clé dans une langue installée (faite dans une
interface anglaise, ou colonne qui devient traduisible) recevrait du catalogue le
français LIVRÉ, qui la masquerait : `post-migrate.py` l'y recopie AVANT le catalogue.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_fundraising_core"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.1.0
LIVRES_FR = {('data/bf_fundraising_data.xml', 'solicit_deceased', 'name'): 'Décédé(e)',
 ('data/bf_fundraising_data.xml', 'solicit_do_not_call', 'name'): 'Ne pas appeler',
 ('data/bf_fundraising_data.xml', 'solicit_do_not_email', 'name'): 'Ne pas envoyer de courriel',
 ('data/bf_fundraising_data.xml', 'solicit_do_not_mail', 'name'): 'Ne pas envoyer de courrier',
 ('data/bf_fundraising_data.xml', 'solicit_do_not_solicit', 'name'): 'Ne pas solliciter',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'button_text'): 'Ouvrir les campagnes',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'description'): '[action:donation.donation_campaign_action] '
                                                                    'Créez une campagne avec un '
                                                                    'objectif, puis ajoutez-y des '
                                                                    'sollicitations et des '
                                                                    'trousses.',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'done_text'): 'Campagne créée',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'title'): 'Lancer une campagne',
 ('data/bf_onboarding.xml', 'bf_onb_step_funds', 'button_text'): 'Ouvrir les fonds',
 ('data/bf_onboarding.xml', 'bf_onb_step_funds', 'description'): '[action:bf_fundraising_core.action_bf_fund] '
                                                                 'Créez vos fonds (désignations) '
                                                                 'et reliez chacun à un compte '
                                                                 'analytique pour la ventilation '
                                                                 'comptable.',
 ('data/bf_onboarding.xml', 'bf_onb_step_funds', 'done_text'): 'Fonds configurés',
 ('data/bf_onboarding.xml', 'bf_onb_step_funds', 'title'): 'Configurer vos fonds',
 ('data/bf_onboarding.xml', 'bf_onb_step_gift', 'button_text'): 'Ouvrir les dons',
 ('data/bf_onboarding.xml', 'bf_onb_step_gift', 'description'): '[action:donation.donation_action] '
                                                                'Saisissez un don, affectez-le à '
                                                                'un fonds et à une campagne, puis '
                                                                'validez-le.',
 ('data/bf_onboarding.xml', 'bf_onb_step_gift', 'done_text'): 'Don enregistré',
 ('data/bf_onboarding.xml', 'bf_onb_step_gift', 'title'): 'Enregistrer un premier don',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Levée de fonds'}


def _porteur(rec):
    """L'enregistrement qui stocke la valeur : un travail planifié, son action."""
    return rec.ir_actions_server_id if rec._name == "ir.cron" else rec


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
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        _porteur(rec_en).write({champ: noeud.text.strip()})
