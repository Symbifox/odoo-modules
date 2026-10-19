"""18.0.1.11.0, étape `post` : protéger les retouches AVANT le catalogue.

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
