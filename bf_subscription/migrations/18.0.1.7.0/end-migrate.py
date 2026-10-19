"""18.0.1.7.0 : les libellés passent d'une source française à une source anglaise.

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

import re

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

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
        if SANS_CLIENTS.sub("", (rec_en[champ] or "").strip()) not in (francais, _forme(rec_en, champ, francais)):
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        _porteur(rec_en).write({champ: noeud.text.strip()})
