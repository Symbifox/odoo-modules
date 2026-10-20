"""18.0.1.5.0, étape `post` : protéger les retouches AVANT le catalogue.

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
        if not retouche or retouche in (francais, anglais, _forme(rec, champ, francais),
                                        _forme(rec, champ, anglais)):
            continue
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.warning("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
