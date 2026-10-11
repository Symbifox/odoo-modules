"""18.0.1.1.0, étape `post` : protéger les retouches AVANT le catalogue.

Odoo charge le catalogue du module juste APRÈS `post`, avant `end` : une valeur
`noupdate` retouchée à la main qui n'a pas de clé dans une langue installée (retouche
faite dans une interface anglaise, ou colonne qui devient traduisible) y recevrait le
français LIVRÉ, qui la masquerait. On y recopie la retouche ici; le catalogue ne
remplace jamais une clé existante d'un enregistrement `noupdate`.

L'anglais livré n'est pas une retouche : un enregistrement supprimé chez un locataire
est recréé par la montée depuis le fichier de données, déjà en anglais.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

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
    arbres = {}
    for (fichier, xmlid, champ), francais in LIVRES_FR.items():
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        if not rec:
            continue
        if fichier not in arbres:
            with file_open(f"{MODULE}/{fichier}", "rb") as f:
                arbres[fichier] = etree.parse(f)
        noeud = arbres[fichier].find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        anglais = (noeud.text or "").strip() if noeud is not None else ""
        porteur = _porteur(rec)
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        valeurs = brut if isinstance(brut, dict) else {"en_US": brut}
        retouche = (valeurs.get("en_US") or "").strip()
        if not retouche or retouche in (francais, anglais):
            continue
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.info("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
