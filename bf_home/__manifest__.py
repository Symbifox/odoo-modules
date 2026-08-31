{
    "name": "Accueil Symbifox",
    "summary": "Écran d'accueil qui répond « qu'est-ce qui a besoin de moi » plutôt que « quelle app je clique »",
    "description": """
Accueil Symbifox
================

Remplace la grille d'applications par un écran ordonné par **qui est bloqué** :
ce qui m'attend, ce qui attend quelqu'un d'autre, l'argent, le risque. Puis,
sous les bandes, les chiffres : argent, exploitation, sécurité.

Principes
---------
* Une bande sans contenu disparaît. Aucune tuile n'affiche « 0 ».
* Chaque nombre ouvre la liste filtrée correspondante, jamais une statistique morte.
* La bande « En attente d'eux » est la vedette : mandats en attente client,
  signatures envoyées et jamais ouvertes, dépôts sécurisés qui expirent. C'est
  ce qu'aucun ERP ne montre, et c'est là que le temps facturable se perd.
* Les applications restent à un raccourci de distance.

Une seule surface
-----------------
Le module ``bf_dashboard`` a été absorbé ici le 2026-08-30. Il y
avait deux écrans qui répondaient à la même question sur les mêmes données, deux
menus, et une porte d'entrée qui n'était ni l'un ni l'autre.

Ce qui a été gardé mot pour mot, et pourquoi : le modèle ``bf.dashboard``, la
signature de ``get_dashboard_data()`` et le nom de gabarit
``bf_dashboard.Dashboard``. Cinq modules les étendent, dont quatre par un xpath
qui s'ancre sur une expression littérale du gabarit. Un xpath d'extension qui ne
résout plus **ne lève pas** : la tuile du module qui hérite disparaît, sans
trace. ``tests/test_dashboard.py`` fige les deux ancres.

Dépendances
-----------
Aucune dépendance dure sur les modules lus. Chaque signal est sondé à l'exécution,
donc le module s'installe sur un locataire qui a trois modules comme sur un qui
les a tous. ``account`` et ``project`` ne font pas exception depuis l'absorption :
les collecteurs comptables passent par la même garde que les autres.
""",
    "version": "18.0.2.0.0",
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
        # No ir.model.access row: bf.home and bf.dashboard are AbstractModels,
        # which have no table and cannot carry one. Access is enforced where it
        # belongs instead : every collector reads through the calling user, so
        # record rules apply and a user without rights on a model simply gets
        # that band omitted.
        "views/home_views.xml",
        "data/bf_home_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_home/static/src/scss/home.scss",
            # Avant home.js, qui l'importe. L'ordre n'est pas requis par le
            # transpileur, il est là pour que la lecture du manifeste dise dans
            # quel sens vont les dépendances.
            "bf_home/static/src/js/bf_dashboard.js",
            "bf_home/static/src/js/home.js",
            "bf_home/static/src/xml/bf_dashboard.xml",
            "bf_home/static/src/xml/home.xml",
        ],
    },
}
