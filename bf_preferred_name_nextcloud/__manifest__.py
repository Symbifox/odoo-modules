# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Contacts Nextcloud",
    # 18.0.1.0.0: premier jet.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Les pronoms et le genre grammatical dans les cartes poussées vers Nextcloud",
    "description": """
Pont Contacts Nextcloud
=======================

Les cartes que ``contacts_nextcloud_sync`` pousse vers le carnet d'adresses
Nextcloud reçoivent, pour chaque personne qui les a donnés, ses pronoms et son
genre grammatical : propriétés ``PRONOUNS`` et ``GRAMGENDER`` de la RFC 9554.

**En sortie seulement** : la synchro
inverse ne les lit pas, les pronoms se donnent dans Odoo.

Seulement quand la synchro POUSSE (sens « Odoo vers Nextcloud ») : dans les
autres sens, la carte de Nextcloud fait foi et elle n'est pas réécrite.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "contacts_nextcloud_sync"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
