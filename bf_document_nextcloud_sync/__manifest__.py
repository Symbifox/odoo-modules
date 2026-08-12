{
    "name": "Document Nextcloud Sync",
    "summary": "Synchronisation documents Odoo-Nextcloud via WebDAV",
    "version": "18.0.1.3.0",
    "category": "Services/Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "project_knowledge_matrix",
        "bf_onboarding_base",
    ],
    "external_dependencies": {
        "python": ["cryptography", "defusedxml", "requests"],
    },
    "data": [
        # Security
        "security/ir.model.access.csv",
        # Wizard
        "wizard/document_nc_upload_wizard_views.xml",
        # Views
        "views/nextcloud_document_config_views.xml",
        "views/project_document_views.xml",
        "views/project_views.xml",
        "views/res_config_settings_views.xml",
        "views/menu.xml",
        # Data
        "data/nextcloud_sync_cron.xml",
        "data/bf_onboarding.xml",
    ],
}
