"""18.0.3.3.1, étape `post` : protéger les retouches AVANT le catalogue.

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

MODULE = "bf_persona"
# (fichier, identifiant, champ) -> français livré avant 18.0.3.3.1
LIVRES_FR = {('data/bf_onboarding.xml', 'bf_onb_step_dashboard', 'button_text'): 'Ouvrir le tableau de bord',
 ('data/bf_onboarding.xml', 'bf_onb_step_dashboard', 'description'): '[action:bf_persona.action_contact_persona_dashboard] '
                                                                     'Validez que les KPIs sont '
                                                                     'visibles et compréhensibles.',
 ('data/bf_onboarding.xml', 'bf_onb_step_dashboard', 'done_text'): 'Tableau de bord validé',
 ('data/bf_onboarding.xml', 'bf_onb_step_dashboard', 'panel_step_open_action_name'): 'bf_open_action',
 ('data/bf_onboarding.xml', 'bf_onb_step_dashboard', 'title'): 'Consulter le tableau de bord',
 ('data/bf_onboarding.xml', 'bf_onb_step_defaults', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_defaults', 'description'): 'Vérifiez que les crons (seed, '
                                                                    'refresh, dégradation) sont '
                                                                    'actifs et alignés sur votre '
                                                                    "cadence d'équipe.",
 ('data/bf_onboarding.xml', 'bf_onb_step_defaults', 'done_text'): 'Automatisations actives',
 ('data/bf_onboarding.xml', 'bf_onb_step_defaults', 'panel_step_open_action_name'): 'bf_open_res_config_settings',
 ('data/bf_onboarding.xml', 'bf_onb_step_defaults', 'title'): 'Activer les automatisations',
 ('data/bf_onboarding.xml', 'bf_onb_step_ntfy', 'button_text'): 'Ouvrir Paramètres',
 ('data/bf_onboarding.xml', 'bf_onb_step_ntfy', 'description'): 'Renseignez la clé webhook ntfy '
                                                                'dans Paramètres pour recevoir les '
                                                                'alertes de dégradation persona.',
 ('data/bf_onboarding.xml', 'bf_onb_step_ntfy', 'done_text'): 'Clé configurée',
 ('data/bf_onboarding.xml', 'bf_onb_step_ntfy', 'panel_step_open_action_name'): 'bf_open_res_config_settings',
 ('data/bf_onboarding.xml', 'bf_onb_step_ntfy', 'title'): 'Configurer la clé ntfy',
 ('data/bf_onboarding.xml', 'bf_onb_step_permissions', 'button_text'): 'Gérer les utilisateurs',
 ('data/bf_onboarding.xml', 'bf_onb_step_permissions', 'description'): '[action:base.action_res_users] '
                                                                       'Donnez le groupe « '
                                                                       'Utilisateur persona » aux '
                                                                       'conseillers et « '
                                                                       'Gestionnaire persona » aux '
                                                                       'responsables.',
 ('data/bf_onboarding.xml', 'bf_onb_step_permissions', 'done_text'): 'Accès distribués',
 ('data/bf_onboarding.xml', 'bf_onb_step_permissions', 'panel_step_open_action_name'): 'bf_open_action',
 ('data/bf_onboarding.xml', 'bf_onb_step_permissions', 'title'): 'Assigner les accès Persona',
 ('data/bf_onboarding.xml', 'bf_onb_step_seed', 'button_text'): 'Créer un persona',
 ('data/bf_onboarding.xml', 'bf_onb_step_seed', 'description'): '[action:bf_persona.action_contact_persona] '
                                                                "Créez un persona à partir d'un "
                                                                'contact clé pour amorcer le '
                                                                'module.',
 ('data/bf_onboarding.xml', 'bf_onb_step_seed', 'done_text'): 'Premier persona créé',
 ('data/bf_onboarding.xml', 'bf_onb_step_seed', 'panel_step_open_action_name'): 'bf_open_action',
 ('data/bf_onboarding.xml', 'bf_onb_step_seed', 'title'): 'Créer un premier persona',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'name'): 'Persona des contacts',
 ('data/bf_onboarding.xml', 'bf_onboarding_panel', 'panel_close_action_name'): 'action_close_panel_bf_persona'}
# Langues dont la clé, si l'en_US lui est égal, est la sortie d'un ancien crochet (pas une retouche).
CROCHET = []
# Anciennes formes LIVRÉES connues (avant, après) : ramenées à l'actuelle avant de comparer.
ANCIENNES = []


def _n(texte):
    for avant, apres in ANCIENNES:
        texte = texte.replace(avant, apres)
    return texte


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


def _contenu(noeud):
    """Le contenu d'un <field> comme Odoo le charge : un champ `type="html"` est la sérialisation
    HTML de ses enfants, sinon le texte."""
    if noeud is None:
        return ""
    if noeud.get("type") == "html":
        return "".join(etree.tostring(n, method="html", encoding="unicode") for n in noeud)
    return noeud.text or ""


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
        anglais = _contenu(noeud).strip()
        porteur = _porteur(rec)
        porteur.flush_recordset([champ])  # la base, pas un tampon de l'ORM
        cr.execute(SQL("SELECT %s FROM %s WHERE id = %s", SQL.identifier(champ),
                       SQL.identifier(porteur._table), porteur.id))
        brut = (cr.fetchone() or [None])[0]
        valeurs = brut if isinstance(brut, dict) else {"en_US": brut}
        retouche = (valeurs.get("en_US") or "").strip()
        if not retouche or _n(retouche) in {_n(x) for x in (francais, anglais, _forme(rec, champ, francais),
                                                          _forme(rec, champ, anglais))}:
            continue
        if any((valeurs.get(l) or "").strip() == retouche for l in CROCHET):
            continue  # sortie d'un ancien crochet, pas une retouche
        manquantes = [lang for lang in langues if lang not in valeurs]
        if manquantes:
            _logger.warning("%s : %s.%s retouché, recopié en %s", MODULE, xmlid, champ, manquantes)
            if callable(porteur._fields[champ].translate):
                # Champ traduit terme à terme : update_field_translations attend des termes, pas une
                # valeur entière (TypeError) ; on recopie la retouche telle quelle par SQL.
                for lang in manquantes:
                    cr.execute(SQL("UPDATE %s SET %s = %s || jsonb_build_object(%s, %s->>'en_US') WHERE id = %s",
                                   SQL.identifier(porteur._table), SQL.identifier(champ), SQL.identifier(champ),
                                   lang, SQL.identifier(champ), porteur.id))
                porteur.invalidate_recordset([champ])
            else:
                porteur.update_field_translations(champ, {lang: valeurs["en_US"] for lang in manquantes})
