{
    "name": "Accueil Symbifox",
    "summary": "Écran d'accueil qui répond « qu'est-ce qui a besoin de moi » plutôt que « quelle app je clique »",
    "description": """
Accueil Symbifox
================

Remplace la grille d'applications par un écran ordonné par **qui est bloqué** :
ce qui m'attend, ce qui attend quelqu'un d'autre, l'argent, le risque.

Principes
---------
* Une bande sans contenu disparaît. Aucune tuile n'affiche « 0 ».
* Chaque nombre ouvre la liste filtrée correspondante, jamais une statistique morte.
* La bande « En attente d'eux » est la vedette : mandats en attente client,
  signatures envoyées et jamais ouvertes, dépôts sécurisés qui expirent. C'est
  ce qu'aucun ERP ne montre, et c'est là que le temps facturable se perd.
* Les applications restent à un raccourci de distance.

Dépendances
-----------
Aucune dépendance dure sur les modules lus. Chaque signal est sondé à l'exécution,
donc le module s'installe sur un locataire qui a trois modules comme sur un qui
les a tous.
""",
    "version": "18.0.1.2.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    # Deliberately minimal. Every signal module is probed at runtime instead, so
    # this installs anywhere and each band appears only when its source exists.
    "depends": ["base", "web", "mail"],
    "data": [
        # No ir.model.access row: bf.home is an AbstractModel, which has no table
        # and cannot carry one. Access is enforced where it belongs instead — every
        # collector reads through the calling user, so record rules apply and a user
        # without rights on a model simply gets that band omitted.
        "views/home_views.xml",
        "data/bf_home_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_home/static/src/scss/home.scss",
            "bf_home/static/src/js/home.js",
            "bf_home/static/src/xml/home.xml",
        ],
    },
}
