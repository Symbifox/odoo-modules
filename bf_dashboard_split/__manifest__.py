{
    "name": "Tableau de bord — vue 3 panneaux",
    "summary": "Vue client Odoo affichant 3 actions Odoo en iframe (haut-gauche, haut-droite, bas) avec séparateurs redimensionnables",
    "version": "18.0.1.4.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "depends": ["web"],
    "data": [
        "views/dashboard_split_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_dashboard_split/static/src/js/dashboard_split.js",
            "bf_dashboard_split/static/src/xml/dashboard_split.xml",
            "bf_dashboard_split/static/src/scss/dashboard_split.scss",
        ],
    },
}
