# -*- coding: utf-8 -*-
{
    "name": "Menu des applications cherchable",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Le menu des applications devient une grille d'icônes cherchable au clavier",
    "description": """
Odoo rend le menu des applications comme une liste de noms, sans icône et sans
recherche. Passé une vingtaine d'applications, la liste dépasse l'écran et il
faut la parcourir des yeux à chaque fois.

Ce module la remplace par un panneau :

* une **grille d'icônes** avec le nom, en deux colonnes ou plus selon la largeur ;
* **défilante**, dans un panneau borné (40% de la largeur, 55% de la hauteur) ;
* un **champ de recherche** qui a déjà le curseur à l'ouverture — on clique, on
  tape, et **Entrée** ouvre la première correspondance ;
* la recherche se **vide au choix d'une tuile**, donc la fois suivante le menu
  est complet et le champ est net.

La recherche est tolérante aux fautes : elle passe par le même `fuzzyLookup`
que la palette de commandes d'Odoo, et classe par pertinence.

Rien d'autre ne change. Les entrées restent des `DropdownItem` : mêmes liens,
même navigation au clavier, même classe ``o_app`` — les tours et les tests qui
s'appuient dessus continuent de fonctionner.

Les couleurs suivent les variables ``--brand-*`` si l'instance en pose, et
retombent sinon sur des valeurs neutres.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "bf_apps_menu/static/src/scss/apps_menu.scss",
            "bf_apps_menu/static/src/xml/apps_menu.xml",
            "bf_apps_menu/static/src/js/apps_menu.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
