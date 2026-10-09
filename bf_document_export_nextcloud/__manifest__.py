{
    "name": "Policies & Procedures Export - Nextcloud",
    "summary": "Deposit the exported registry in a Nextcloud folder, and read the "
               "Nextcloud files behind external documents",
    "version": "18.0.1.2.0",
    "description": """
Export du registre : Nextcloud
==============================

Pont entre l'export du registre et la synchronisation Nextcloud des documents.

Déposer la copie publiée
------------------------
Chaque export complet d'un gabarit est déposé dans un dossier de Nextcloud. Seul ce qui
a changé est écrit, un fichier modifié sur Nextcloud n'est jamais écrasé, et un document
qui quitte le registre est déplacé aux archives, jamais supprimé. Seul un export lancé au
nom de la personne « Exporter au nom de » du gabarit est déposé. Le dépôt est refusé
dans un dossier qui chevauche un dossier source.

Lire les fichiers sources
-------------------------
À l'export, le pont lit le fichier Nextcloud vivant des documents dont le corps y est
rangé, et le range sous le nom stable du document. Il ne lit que par la configuration
source et sous les dossiers sources nommés sur le gabarit par un gestionnaire : le
compte de service de la configuration lit tout, quelle que soit la personne qui a relié
le fichier.
""",
    "category": "Services/Project",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    # Bridge: installs by itself where both sides are present.
    "depends": ["bf_document_export", "bf_document_nextcloud_sync"],
    "data": [
        "security/ir.model.access.csv",
        "views/export_template_views.xml",
    ],
    "application": False,
    "auto_install": True,
    "installable": True,
}
