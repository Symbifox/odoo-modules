{
    "name": "BF Bureau — vues multi-panneaux",
    "summary": "Crée et gère des « bureaux » nommés (mises en page multi-panneaux d'actions Odoo) modifiables depuis l'UI",
    "version": "18.0.3.2.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    'license': 'LGPL-3',
    "application": True,
    "installable": True,
    # ⚠️ `bf_email_management` reste volontairement ABSENT : ce module est
    # LGPL-3, celui-là est BUSL-1.1, et une dépendance dure ferait promettre à
    # la licence permissive ce qu'elle ne peut pas tenir. La tuile courriel du
    # bureau par défaut est posée à l'installation par `hooks.py`.
    "depends": ["web", "base", "mail", "project", "bf_onboarding_base"],
    "post_init_hook": "post_init_hook",
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
