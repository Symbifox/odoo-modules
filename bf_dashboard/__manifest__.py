{
    "name": "Tableau de bord Blue Fox",
    "summary": "Tableau de bord unifi\u00e9 agr\u00e9geant facturation, h\u00e9bergement, connaissances et vie priv\u00e9e",
    "version": "18.0.1.0.0",
    "category": "Services",
    "website": "https://bluefoxconsultant.com",
    "author": "Blue Fox Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "base",
        "account",
        "project",
        "mail",
        "hosting_management",
        "project_knowledge_matrix",
        "privacy_consent",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/dashboard_views.xml",
        "data/dashboard_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_dashboard/static/src/js/bf_dashboard.js",
            "bf_dashboard/static/src/xml/bf_dashboard.xml",
        ],
    },
    "post_init_hook": "_set_home_action",
}
