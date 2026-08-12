{
    "name": "Nextcloud File Browser",
    "summary": "Navigateur WebDAV Nextcloud embarque dans les fiches Odoo (projets, taches) + application autonome",
    "version": "18.0.3.3.0",
    "category": "Services/Project",
    "website": "https://bluefoxconsultant.com",
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
        "views/nextcloud_document_config_views.xml",
        "views/project_views.xml",
        "views/project_task_views.xml",
        "views/nc_browser_app.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_nextcloud_browser/static/src/scss/nc_browser.scss",
            "bf_nextcloud_browser/static/src/js/nc_browser.js",
            "bf_nextcloud_browser/static/src/js/nc_systray.js",
            "bf_nextcloud_browser/static/src/xml/nc_browser.xml",
        ],
    },
}
