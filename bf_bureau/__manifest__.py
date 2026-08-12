{
    "name": "BF Bureau — vues multi-panneaux",
    "summary": "Crée et gère des « bureaux » nommés (mises en page multi-panneaux d'actions Odoo) modifiables depuis l'UI",
    "version": "18.0.3.1.0",
    "category": "Productivity",
    "website": "https://bluefoxconsultant.com",
    "author": "Blue Fox Inc.",
    'license': 'LGPL-3',
    "application": True,
    "installable": True,
    "depends": ["web", "base", "mail", "project", "bf_email_management", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_bureau_security.xml",
        "views/bf_bureau_views.xml",
        "views/bf_bureau_menu.xml",
        "data/bf_bureau_default_desk.xml",
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_bureau/static/src/js/bf_bureau_desk.js",
            "bf_bureau/static/src/xml/bf_bureau_desk.xml",
            "bf_bureau/static/src/scss/bf_bureau_desk.scss",
        ],
    },
}
