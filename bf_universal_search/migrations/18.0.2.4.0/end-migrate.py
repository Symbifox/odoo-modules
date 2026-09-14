"""18.0.2.4.0 : les libellés passent d'une source française à une source anglaise.

Odoo ne traduit jamais vers en_US, la langue source : tant que la source était
française, un usager réglé en anglais lisait la recherche en français.

🔴 Une mise à jour n'écrase jamais une traduction existante. On recharge le
catalogue du module en écrasant.

Le libellé des groupes de résultats (`name` des fiches de recherche) devient
traduisible. Ces fiches sont créées par le crochet d'installation, en
`noupdate` : la colonne convertie porte le nom français en en_US. On la bascule
SEULEMENT si elle porte encore un nom livré (le français de 18.0.2.x, ou
l'anglais de 18.0.1.3.0), puis on écrit chaque langue installée. Un nom changé
à la main est laissé tel quel.

⚠️ En `end` : Odoo charge les traductions du module juste après le `post`.
"""

import logging

from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_open

from odoo.addons.bf_universal_search.hooks import _SEARCH_CONFIGS, write_label_translations

_logger = logging.getLogger(__name__)

MODULE = "bf_universal_search"
DONNEES = "data/bf_onboarding.xml"
# Le panneau de prise en main, donnée `noupdate` livrée en français.
LIVRES_FR = {
    ("bf_onb_step_settings", "title"): "Configurer la recherche universelle",
    ("bf_onb_step_settings", "description"):
        "Choisissez les modèles indexés et les priorités d'affichage dans la barre Cmd+K.",
    ("bf_onb_step_settings", "button_text"): "Ouvrir Paramètres",
    ("bf_onb_step_settings", "done_text"): "Recherche calibrée",
    ("bf_onboarding_panel", "name"): "Recherche universelle",
}

# Les noms livrés en français jusqu'à 18.0.2.3.0.
NOMS_LIVRES_FR = {
    "contacts": "Contacts", "crm_leads": "Opportunités", "projects": "Projets",
    "tasks": "Tâches", "meetings": "Rencontres", "agendas": "Ordres du jour",
    "emails": "Courriels", "sms": "SMS", "calls": "Appels", "services": "Services",
    "servers": "Serveurs", "domains": "Domaines", "software": "Logiciels",
    "documents": "Documents", "matrices": "Matrices de connaissances",
    "knowledge": "Connaissances", "resolutions": "Résolutions corporatives",
    "signatures": "Demandes de signature", "transfers": "Transferts sécurisés",
    "invoices": "Factures", "tickets": "Tickets", "calendar": "Événements",
    "blog": "Articles de blogue", "products": "Produits et services",
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
    for spec in _SEARCH_CONFIGS:
        config = env.ref(f"{MODULE}.search_config_{spec['suffix']}", raise_if_not_found=False)
        if not config:
            continue
        anglais = spec["name"]._translate("en_US")
        actuel = config.with_context(lang="en_US").name
        if actuel not in (NOMS_LIVRES_FR.get(spec["suffix"]), anglais):
            _logger.info("%s : fiche %s renommée à la main, laissée telle quelle",
                         MODULE, spec["suffix"])
            continue
        config.with_context(lang="en_US").write({"name": anglais})
        write_label_translations(env, config, spec)
    _basculer_donnees_noupdate(env)


def _basculer_donnees_noupdate(env):
    """Passe en anglais la valeur en_US des données `noupdate` encore livrées.

    Le texte anglais est lu dans le fichier de données du module; une valeur
    retouchée à la main (qui ne vaut plus le français livré) est laissée.
    """
    with file_open(f"{MODULE}/{DONNEES}", "rb") as f:
        arbre = etree.parse(f)
    for (xmlid, champ), francais in LIVRES_FR.items():
        rec = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        noeud = arbre.find(f".//record[@id=\x27{xmlid}\x27]/field[@name=\x27{champ}\x27]")
        if not rec or noeud is None or not noeud.text:
            continue
        rec_en = rec.with_context(lang="en_US")
        if (rec_en[champ] or "").strip() != francais:
            _logger.info("%s : %s.%s retouché à la main, laissé tel quel", MODULE, xmlid, champ)
            continue
        rec_en.write({champ: noeud.text.strip()})
