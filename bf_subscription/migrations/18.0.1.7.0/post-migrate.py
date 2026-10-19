"""18.0.1.7.0, étape `post` : protéger les retouches AVANT le catalogue.

Odoo charge le catalogue du module juste APRÈS `post`, avant `end` : une valeur
`noupdate` retouchée à la main qui n'a pas de clé dans une langue installée (retouche
faite dans une interface anglaise, ou colonne qui devient traduisible) y recevrait le
français LIVRÉ, qui la masquerait. On y recopie la retouche ici; le catalogue ne
remplace jamais une clé existante d'un enregistrement `noupdate`.

L'anglais livré n'est pas une retouche : un enregistrement supprimé chez un locataire
est recréé par la montée depuis le fichier de données, déjà en anglais.
"""

import logging

import re

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import SQL, file_open

_logger = logging.getLogger(__name__)

MODULE = "bf_subscription"
# La curation de publication a retiré la liste de clients d'un texte livré (« d'un client
# (…, …), ») : la forme d'avant la curation est aussi « non retouchée ».
SANS_CLIENTS = re.compile(r" \([^()]*…\)")
# (fichier, identifiant, champ) -> français livré avant 18.0.1.7.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_list', 'button_text'): 'Ouvrir Abonnements',
 ('data/bf_onboarding.xml', 'bf_onb_step_list', 'description'): '[action:bf_subscription.action_subscription_subscription] '
                                                                'Créez une fiche pour chaque '
                                                                'abonnement récurrent : SaaS, '
                                                                'memberships, infrastructure.',
 ('data/bf_onboarding.xml', 'bf_onb_step_list', 'done_text'): 'Abonnements listés',
 ('data/bf_onboarding.xml', 'bf_onb_step_list', 'title'): 'Lister vos abonnements actifs',
 ('data/bf_onboarding.xml', 'bf_onb_step_managed_for', 'button_text'): 'Voir les abonnements',
 ('data/bf_onboarding.xml', 'bf_onb_step_managed_for', 'description'): '[action:bf_subscription.action_subscription_subscription] '
                                                                       'Pour chaque abonnement '
                                                                       "géré au nom d'un client"
                                                                       ', cochez « '
                                                                       'Géré pour le client » et '
                                                                       'activez la refacturation.',
 ('data/bf_onboarding.xml', 'bf_onb_step_managed_for', 'done_text'): 'Refacturation configurée',
 ('data/bf_onboarding.xml', 'bf_onb_step_managed_for', 'title'): 'Configurer la refacturation pour '
                                                                 'les clients',
 ('data/bf_onboarding.xml', 'bf_onb_step_renewals', 'button_text'): 'Voir les renouvellements',
 ('data/bf_onboarding.xml', 'bf_onb_step_renewals', 'description'): '[action:bf_subscription.action_subscription_renewals_30d] '
                                                                    'Survol des abonnements qui '
                                                                    'arrivent à échéance dans les '
                                                                    '30 prochains jours.',
 ('data/bf_onboarding.xml', 'bf_onb_step_renewals', 'done_text'): 'Renouvellements survolés',
 ('data/bf_onboarding.xml', 'bf_onb_step_renewals', 'title'): 'Vérifier les renouvellements à '
                                                              'venir',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Abonnements',
 ('data/subscription_activity_data.xml', 'mail_activity_type_renewal', 'name'): 'Renouvellement '
                                                                                "d'abonnement",
 ('data/subscription_cron.xml', 'cron_subscription_digest', 'name'): 'Abonnements : Envoi des '
                                                                     'récapitulatifs',
 ('data/subscription_cron.xml', 'cron_subscription_renewal_alerts', 'name'): 'Abonnements : '
                                                                             'Relances de '
                                                                             'renouvellement',
 ('data/subscription_tag_data.xml', 'tag_certificate', 'name'): 'Certificat',
 ('data/subscription_tag_data.xml', 'tag_critical', 'name'): 'Critique',
 ('data/subscription_tag_data.xml', 'tag_domain', 'name'): 'Nom de domaine',
 ('data/subscription_tag_data.xml', 'tag_infrastructure', 'name'): 'Infrastructure',
 ('data/subscription_tag_data.xml', 'tag_membership', 'name'): 'Membership',
 ('data/subscription_tag_data.xml', 'tag_saas', 'name'): 'SaaS'}



def _porteur(rec):
    """L'enregistrement qui stocke la valeur : un travail planifié, son action."""
    return rec.ir_actions_server_id if rec._name == "ir.cron" else rec


def _forme(rec, champ, valeur):
    """La valeur telle qu'Odoo la range. 🔴 Un champ HTML est ASSAINI à l'écriture : le corps
    d'un gabarit de courriel en base n'est jamais égal au texte brut du fichier de données, et la
    comparaison brute le prenait pour une retouche (corps laissés
    en français en en_US, puis recopiés dans les autres langues)."""
    try:
        return str(rec._fields[champ].convert_to_column(valeur, rec) or "").strip()
    except Exception:  # noqa: BLE001
        return valeur


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
        if not retouche or SANS_CLIENTS.sub("", retouche) in (francais, anglais, _forme(rec, champ, francais),
                                                                _forme(rec, champ, anglais)):
            continue
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.warning("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
