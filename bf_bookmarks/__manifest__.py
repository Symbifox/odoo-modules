# -*- coding: utf-8 -*-
{
    "name": "Signets partagés",
    "version": "18.0.1.5.4",
    "category": "Productivity",
    "summary": "Les signets d'entreprise, d'équipe et perso, gardés dans Odoo et "
               "portés au navigateur par l'extension Symbifox",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "application": True,
    "installable": True,
    "auto_install": False,
    "depends": ["hr", "project", "mail"],
    "description": """
Signets partagés
================

Chaque personne a ses signets, et chaque organisation a les siens : le portail du
client, la documentation interne, les outils d'une équipe. Ceux-là vivent d'ordinaire
dans un courriel d'accueil, un document « liens utiles » ou la mémoire de quelqu'un.

Ce module les garde dans Odoo, en trois portées :

* **Entreprise** : vus par tout le personnel de la société.
* **Équipe** : vus par les membres d'un département, ou par les personnes qui
  suivent un projet.
* **Perso** : vus par leur propriétaire seulement.

Qui modifie quoi
----------------
Les signets d'entreprise ne se modifient que par les responsables des signets. Ceux
d'un département, par son gestionnaire ; ceux d'un projet, par son chargé de projet.
Les signets perso, par leur propriétaire. Le droit est vérifié par le serveur, sur
l'état d'avant ET sur l'état d'après : déplacer un signet perso dans le dossier de
l'entreprise est refusé à qui n'a pas le droit d'y écrire.

Dans le navigateur
------------------
L'extension Symbifox Signets (Chrome, Edge, Brave, Firefox) recopie les dossiers dans
le navigateur. Ce qu'on n'a pas le droit de modifier y est remis en place à chaque
synchronisation ; ce qu'on modifie dans ses signets perso remonte à Odoo.

Dans Odoo
---------
Une icône Signets dans la barre du haut : recherche et accès direct, sans quitter la
page. La page Signets (/odoo/signets) montre une carte par section et s'installe comme
application sur un téléphone. Un bouton « Signets » sur chaque projet et chaque fiche
client ouvre les signets de l'équipe du projet.

Une racine d'entreprise est créée pour chaque société, avec un premier signet vers
Odoo. Ce qui est supprimé depuis le navigateur est archivé, pas effacé, et se restaure
depuis « Supprimés depuis le navigateur ».

Importer
--------
Un fichier de signets au format Netscape (l'export de Chrome, Firefox, Edge, ou la
sauvegarde nocturne de l'app Bookmarks de Nextcloud) s'importe dans n'importe quel
dossier où on a le droit d'écrire. L'import peut se rejouer : un signet déjà présent
au même endroit n'est pas recréé.
""",
    "data": [
        "security/bf_bookmarks_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_bookmarks_rules.xml",
        "data/ir_cron.xml",
        "wizard/bookmark_import_views.xml",
        "views/bookmark_views.xml",
        "views/bookmark_folder_views.xml",
        "views/bookmarks_home_action.xml",
        "views/project_partner_views.xml",
        "views/bookmark_usage_views.xml",
        "views/bookmark_proposal_views.xml",
        "views/bookmark_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bookmarks/static/src/js/bookmarks_data.js",
            "bf_bookmarks/static/src/js/bookmarks_tree.js",
            "bf_bookmarks/static/src/js/bookmarks_systray.js",
            "bf_bookmarks/static/src/js/bookmarks_home.js",
            "bf_bookmarks/static/src/js/bookmarks_palette.js",
            "bf_bookmarks/static/src/xml/bookmarks.xml",
            "bf_bookmarks/static/src/scss/bookmarks.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
}
