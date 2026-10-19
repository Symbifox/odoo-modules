"""18.0.1.11.0 : les libellés passent d'une source française à une source anglaise.

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

MODULE = "bf_editorial"
# (fichier, identifiant, champ) -> français livré avant 18.0.1.11.0
LIVRES_FR = {('data/bf_color_rules.xml', 'bf_color_rule_entry_pillar', 'name'): 'Entrées éditoriales selon le '
                                                                    'pilier',
 ('data/bf_editorial_data.xml', 'checklist_tpl_alt', 'name'): 'Texte alternatif rédigé dans chaque '
                                                              'langue',
 ('data/bf_editorial_data.xml', 'checklist_tpl_blurbs', 'name'): 'Blurbs rédigés dans chaque '
                                                                 'langue',
 ('data/bf_editorial_data.xml', 'checklist_tpl_crosslink', 'name'): 'Liens croisés posés dans les '
                                                                    'articles cités',
 ('data/bf_editorial_data.xml', 'checklist_tpl_images', 'name'): 'Visuels produits et placés',
 ('data/bf_editorial_data.xml', 'checklist_tpl_og', 'name'): 'Image Open Graph définie',
 ('data/bf_editorial_data.xml', 'checklist_tpl_read', 'name'): 'Relecture humaine faite',
 ('data/bf_editorial_data.xml', 'cron_publish_scheduled', 'name'): 'Atelier éditorial : '
                                                                   'publications différées',
 ('data/bf_editorial_data.xml', 'stage_abandoned', 'name'): 'Abandonné',
 ('data/bf_editorial_data.xml', 'stage_blocked', 'name'): 'Bloqué',
 ('data/bf_editorial_data.xml', 'stage_brief', 'description'): 'Angle, promesse et audience '
                                                               'arrêtés.',
 ('data/bf_editorial_data.xml', 'stage_brief', 'name'): 'Brief',
 ('data/bf_editorial_data.xml', 'stage_draft', 'description'): 'Le texte existe dans Odoo, non '
                                                               'publié.',
 ('data/bf_editorial_data.xml', 'stage_draft', 'name'): 'Brouillon',
 ('data/bf_editorial_data.xml', 'stage_factcheck', 'description'): 'Affirmations et sources en '
                                                                   'vérification.',
 ('data/bf_editorial_data.xml', 'stage_factcheck', 'name'): 'Fact-check',
 ('data/bf_editorial_data.xml', 'stage_idea', 'description'): "Un sujet identifié, rien d'écrit.",
 ('data/bf_editorial_data.xml', 'stage_idea', 'name'): 'Idée',
 ('data/bf_editorial_data.xml', 'stage_published', 'name'): 'Publié',
 ('data/bf_editorial_data.xml', 'stage_qa', 'name'): 'QA',
 ('data/bf_editorial_data.xml', 'stage_ready', 'description'): 'Garde de pré-vol verte, en attente '
                                                               "d'un créneau.",
 ('data/bf_editorial_data.xml', 'stage_ready', 'name'): 'Prêt',
 ('data/bf_editorial_data.xml', 'stage_scheduled', 'description'): "Date approuvée, le cron s'en "
                                                                   'charge.',
 ('data/bf_editorial_data.xml', 'stage_scheduled', 'name'): 'Planifié',
 ('data/bf_editorial_data.xml', 'stage_selected', 'description'): "Le sujet passe, l'angle se "
                                                                  'cherche.',
 ('data/bf_editorial_data.xml', 'stage_selected', 'name'): 'Retenu',
 ('data/bf_editorial_data.xml', 'stage_writing', 'name'): 'Rédaction',
 ('security/bf_editorial_security.xml', 'group_editorial_manager', 'comment'): 'Règle les '
                                                                               'calendriers, '
                                                                               'approuve les dates '
                                                                               'et publie.',
 ('security/bf_editorial_security.xml', 'group_editorial_manager', 'name'): 'Direction éditoriale',
 ('security/bf_editorial_security.xml', 'group_editorial_user', 'comment'): 'Écrit, prépare et '
                                                                            'fait avancer les '
                                                                            'entrées, sans pouvoir '
                                                                            'publier.',
 ('security/bf_editorial_security.xml', 'group_editorial_user', 'name'): 'Rédaction',
 ('security/bf_editorial_security.xml', 'module_category_editorial', 'description'): 'Calendrier '
                                                                                     'éditorial et '
                                                                                     'diffusion',
 ('security/bf_editorial_security.xml', 'module_category_editorial', 'name'): 'Atelier éditorial'}


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
