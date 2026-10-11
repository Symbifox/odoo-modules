"""18.0.2.1.0, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_letter_writer"
# (fichier, identifiant, champ) -> français livré avant 18.0.2.1.0
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_first_letter', 'button_text'): 'Rédiger une lettre',
 ('data/bf_onboarding.xml', 'bf_onb_step_first_letter', 'description'): '[action:bf_letter_writer.action_letter_document] '
                                                                        'Créez une lettre, '
                                                                        'appliquez un modèle et '
                                                                        'générez le PDF brandé.',
 ('data/bf_onboarding.xml', 'bf_onb_step_first_letter', 'done_text'): 'Première lettre rédigée',
 ('data/bf_onboarding.xml', 'bf_onb_step_first_letter', 'title'): 'Rédiger une première lettre',
 ('data/bf_onboarding.xml', 'bf_onb_step_letterhead', 'button_text'): 'Ouvrir la société',
 ('data/bf_onboarding.xml', 'bf_onb_step_letterhead', 'description'): '[action:base.action_res_company_form] '
                                                                      'Ouvrez la fiche de votre '
                                                                      'société, onglet « Lettres » '
                                                                      ': signataire, image de '
                                                                      'signature, mention '
                                                                      "d'en-tête et pied de page.",
 ('data/bf_onboarding.xml', 'bf_onb_step_letterhead', 'done_text'): 'En-tête configuré',
 ('data/bf_onboarding.xml', 'bf_onb_step_letterhead', 'title'): "Configurer l'en-tête de lettre",
 ('data/bf_onboarding.xml', 'bf_onb_step_templates', 'button_text'): 'Voir les modèles',
 ('data/bf_onboarding.xml', 'bf_onb_step_templates', 'description'): '[action:bf_letter_writer.action_letter_template] '
                                                                     'Préparez vos modèles '
                                                                     'réutilisables avec champs de '
                                                                     'fusion.',
 ('data/bf_onboarding.xml', 'bf_onb_step_templates', 'done_text'): 'Modèles prêts',
 ('data/bf_onboarding.xml', 'bf_onb_step_templates', 'title'): 'Créer des modèles de lettres',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Rédacteur de lettres'}


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
