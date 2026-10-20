"""18.0.1.5.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_outreach"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.5.0
LIVRES_FR = {('data/bf_color_rules.xml', 'bf_color_rule_target_user', 'name'): 'Cibles selon le responsable',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'button_text'): 'Voir les campagnes',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'description'): '[action:bf_outreach.action_bf_outreach_campaign] '
                                                                    'Définissez la période, '
                                                                    "l'équipe et la cadence de "
                                                                    "relance (combien d'appels et "
                                                                    'de courriels, à quel '
                                                                    'intervalle).',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'done_text'): 'Campagne créée',
 ('data/bf_onboarding.xml', 'bf_onb_step_campaign', 'title'): 'Créer une campagne',
 ('data/bf_onboarding.xml', 'bf_onb_step_targets', 'button_text'): 'Voir les cibles',
 ('data/bf_onboarding.xml', 'bf_onb_step_targets', 'description'): '[action:bf_outreach.action_bf_outreach_target] '
                                                                   'Ajoutez les cibles depuis vos '
                                                                   'contacts, ou sélectionnez-les '
                                                                   'dans la liste des contacts et '
                                                                   "utilisez l'action « Ajouter à "
                                                                   'une campagne de démarchage ».',
 ('data/bf_onboarding.xml', 'bf_onb_step_targets', 'done_text'): 'Cibles chargées',
 ('data/bf_onboarding.xml', 'bf_onb_step_targets', 'title'): 'Charger la liste de cibles',
 ('data/bf_onboarding.xml', 'bf_onb_step_touches', 'button_text'): 'Voir les interactions',
 ('data/bf_onboarding.xml', 'bf_onb_step_touches', 'description'): '[action:bf_outreach.action_bf_outreach_touch] '
                                                                   'Chaque appel et chaque '
                                                                   'courriel se note en deux clics '
                                                                   '; les prochaines relances se '
                                                                   'calculent seules.',
 ('data/bf_onboarding.xml', 'bf_onb_step_touches', 'done_text'): 'Suivi en marche',
 ('data/bf_onboarding.xml', 'bf_onb_step_touches', 'title'): 'Journaliser les contacts',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Démarchage',
 ('data/outreach_stage_data.xml', 'stage_contacted', 'description'): 'Au moins une tentative '
                                                                     'faite, sans réponse encore.',
 ('data/outreach_stage_data.xml', 'stage_contacted', 'name'): 'Contactée',
 ('data/outreach_stage_data.xml', 'stage_discussion', 'description'): 'La cible a répondu et '
                                                                      "l'échange est en cours.",
 ('data/outreach_stage_data.xml', 'stage_discussion', 'name'): 'En discussion',
 ('data/outreach_stage_data.xml', 'stage_lost', 'description'): 'Dossier abandonné : la cadence de '
                                                                "relance s'arrête.",
 ('data/outreach_stage_data.xml', 'stage_lost', 'name'): 'Non retenue',
 ('data/outreach_stage_data.xml', 'stage_meeting', 'description'): 'Une rencontre est planifiée.',
 ('data/outreach_stage_data.xml', 'stage_meeting', 'name'): 'Rendez-vous fixé',
 ('data/outreach_stage_data.xml', 'stage_to_contact', 'description'): 'Cible ajoutée à la '
                                                                      'campagne, aucun contact '
                                                                      'encore fait.',
 ('data/outreach_stage_data.xml', 'stage_to_contact', 'name'): 'À contacter',
 ('data/outreach_stage_data.xml', 'stage_won', 'description'): 'Dossier gagné : la cadence de '
                                                               "relance s'arrête.",
 ('data/outreach_stage_data.xml', 'stage_won', 'name'): 'Convertie'}


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
        if (rec_en[champ] or "").strip() not in (francais, _forme(rec_en, champ, francais)):
            # Ses langues manquantes ont reçu la retouche à l'étape `post`.
            _logger.warning("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        # ⚠️ Un travail planifié s'écrit par son action : ir.cron.write prend un verrou
        # NOWAIT qui ferait ÉCHOUER la montée si le travail tourne à ce moment (le recalcul
        # de `cron_name` réécrit quand même sa ligne : la montée ATTEND le travail).
        _porteur(rec_en).write({champ: noeud.text.strip()})
    # Le nom de la recherche universelle était semé en français comme valeur source : le crochet
    # d'installation ne rejoue pas sur une mise à jour, on le rappelle (idempotent, laisse une
    # valeur retouchée par le client).
    from odoo.addons.bf_outreach.models.post_init import register_universal_search

    register_universal_search(env)
