{
    "name": "Boîte de réception bf.email — barre Odoo",
    "summary": "Bouton systray ouvrant la Boîte de réception bf.email avec compteur (lus + non lus)",
    # 18.0.2.2.1: le bouton Courriels du systray ne retient plus l'affichage du client web : ses appels
    #   partent sans être attendus dans onWillStart. Loin du serveur,
    #   chaque aller-retour en chaîne retardait l'écran demandé.
    # 18.0.2.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.2.3.0",
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
