{
    "name": "Nextcloud File Browser",
    "summary": "Navigateur WebDAV Nextcloud embarque dans les fiches Odoo (projets, taches) + application autonome",
    # 18.0.4.1.2: le bouton du systray ne retient plus l'affichage du client web : ses appels
    #   partent sans être attendus dans onWillStart. Loin du serveur,
    #   chaque aller-retour en chaîne retardait l'écran demandé.
    "version": "18.0.4.1.2",
    "category": "Services/Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "depends": [
        "bf_document_nextcloud_sync",
        "project",
        "project_knowledge_matrix",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "security/bf_nc_browser_security.xml",
        "security/ir.model.access.csv",
        "security/bf_nc_user_credential_security.xml",
        "views/nextcloud_document_config_views.xml",
        "views/project_views.xml",
        "views/project_task_views.xml",
        "views/nc_browser_app.xml",
        "views/nc_user_credential_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_nextcloud_browser/static/src/scss/nc_browser.scss",
            "bf_nextcloud_browser/static/src/js/nc_browser.js",
            "bf_nextcloud_browser/static/src/js/nc_panel.js",
            "bf_nextcloud_browser/static/src/js/nc_systray.js",
            "bf_nextcloud_browser/static/src/js/nc_links.js",
            "bf_nextcloud_browser/static/src/xml/nc_browser.xml",
            "bf_nextcloud_browser/static/src/xml/nc_links.xml",
        ],
    },
}
