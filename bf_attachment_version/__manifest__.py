{
    "name": "Versionnement des pièces jointes",
    "version": "18.0.1.1.0",
    "category": "Technical",
    "summary": "Rien ne se perd quand une pièce jointe est réécrite",
    "description": """
Versionnement des pièces jointes
================================

``ir.attachment`` n'a aucun historique. Quand un éditeur bureautique
enregistre, quand un robot dépose une nouvelle version d'un livrable, ou quand
un appel API réécrit ``raw``, l'ancien contenu disparaît sans trace et sans
retour arrière.

Ce module conserve l'état antérieur. À chaque remplacement du contenu d'une
pièce jointe admissible, il crée un enregistrement ``bf.attachment.version``
qui pointe vers une pièce jointe de conservation portant les octets d'avant.

Ce que ça ne coûte pas
----------------------

Le magasin de fichiers d'Odoo range par empreinte sha1 et son ramasse-miettes
garde tout fichier encore référencé. La pièce de conservation partage donc le
fichier existant : aucun octet de plus sur le disque au moment de l'instantané,
et seulement une ligne en base. Le coût n'apparaît que lorsque le contenu
diverge vraiment, ce qui est précisément le moment où on veut le payer.

Ce qui est versionné, et ce qui ne l'est pas
--------------------------------------------

Seules les pièces de type binaire, sans ``res_field`` (donc jamais le stockage
d'un champ binaire), dont l'extension figure dans la liste configurée, et dont
le modèle n'est pas exclu.

Jamais quand le contenu entrant est identique : une réécriture à l'identique ne
remplace rien.

Accès
-----

Une version n'est visible que par qui peut lire la pièce jointe d'origine. Le
contrôle suit la pièce parente, il ne s'y substitue pas.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["base", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_attachment_version_params.xml",
        "data/ir_cron.xml",
        "views/attachment_version_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_attachment_version/static/src/js/attachment_version.js",
            "bf_attachment_version/static/src/xml/attachment_version.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
