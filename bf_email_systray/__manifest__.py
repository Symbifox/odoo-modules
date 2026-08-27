{
    "name": "Boîte de réception bf.email — barre Odoo",
    "summary": "Bouton systray ouvrant la Boîte de réception bf.email avec compteur (lus + non lus)",
    "version": "18.0.1.4.0",
    "category": "Tools",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": ["web", "bf_email_management"],
    "assets": {
        "web.assets_backend": [
            "bf_email_systray/static/src/js/bf_email_systray.js",
            "bf_email_systray/static/src/xml/bf_email_systray.xml",
        ],
    },
}
