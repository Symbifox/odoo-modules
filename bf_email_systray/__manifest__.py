{
    "name": "Boîte de réception bf.email — barre Odoo",
    "summary": "Bouton systray ouvrant la Boîte de réception bf.email avec compteur (lus + non lus)",
    "version": "18.0.2.0.0",
    "category": "Tools",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": ["web", "bf_email_management"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_email_systray/static/src/scss/bf_email_systray.scss",
            "bf_email_systray/static/src/js/bf_email_panel.js",
            "bf_email_systray/static/src/js/bf_email_systray.js",
            "bf_email_systray/static/src/xml/bf_email_systray.xml",
        ],
    },
}
