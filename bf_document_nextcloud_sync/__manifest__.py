{
    "name": "Document Nextcloud Sync",
    "summary": "Synchronisation documents Odoo-Nextcloud via WebDAV",
    # 18.0.1.6.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.6.1: la traduction française retrouve ses accents (« modifié », « Vérification »,
    #   « téléversé »…), rechargée en écrasant à la montée, nom du travail planifié compris.
    # 18.0.1.6.2: typographie de la traduction française : une espace avant le deux-points, « ex. »,
    #   et des coquilles (« ID de fichier Nextcloud », « Archivé », « Connexion réussie. »,
    #   nom du travail planifié).
    "version": "18.0.1.6.2",
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
