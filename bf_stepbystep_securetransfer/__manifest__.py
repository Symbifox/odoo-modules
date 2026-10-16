{
    "name": "Progression: deposit through secure transfer storage",
    "summary": "Client portal deposits go straight to the S3 storage of the secure "
               "transfer when it is configured; large files stay there.",
    # 18.0.1.0.0 : premier lot. Le navigateur téléverse dans le
    #   seau S3 du socle bf_securetransfer ; jusqu'au seuil de versement (100 Mo)
    #   le fichier devient une pièce jointe de la tâche et l'objet est purgé,
    #   au-delà il reste dans le seau jusqu'à son échéance.
    # 18.0.1.0.1 : le script se branche aussi quand la page est déjà prête (le paquet
    #   public se charge en defer async, après DOMContentLoaded).
    # 18.0.1.0.2 : le bouton d'envoi naît désactivé et le script l'active ; avant, un
    #   clic d'avant le chargement du script rechargeait la page sans rien déposer.
    "version": "18.0.1.0.2",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "installable": True,
    # Pas d'auto_install : le pont ne sert qu'aux locataires qui ont un S3 configuré
    # pour le transfert sécurisé. L'installer est un choix, locataire par locataire.
    "auto_install": False,
    "depends": [
        "bf_stepbystep_clients",
        "bf_securetransfer",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/progression_security.xml",
        "data/ir_cron.xml",
        "views/progression_portal_templates.xml",
        "views/project_project_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_stepbystep_securetransfer/static/src/js/progression_s3_upload.js",
        ],
    },
}
