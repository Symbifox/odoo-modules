{
    "name": "Collabora Online, correctifs Blue Fox",
    "version": "18.0.1.2.0",
    "category": "Technical",
    "summary": "Quatre correctifs au connecteur Collabora amont, sans le forker",
    "description": """
Collabora Online, correctifs Blue Fox
=====================================

Le connecteur ``collabora_odoo`` est publié par Collabora Productivity sous
MPL-2.0 et son README dit qu'il n'a pas encore reçu de revue de sécurité. On le
garde intact pour continuer à profiter de ses mises à jour, et ce module pose
par-dessus les trois correctifs relevés en le lisant ligne par ligne.

1. Le bouton « Modifier » ne ment plus
--------------------------------------

Amont, ``can_write_doc`` rend une chaîne JSON, alors que le JavaScript y lit un
attribut d'objet ; et la fonction cliente est ``async``, donc le ``t-if`` du
gabarit reçoit une promesse, toujours vraie. Le bouton d'édition apparaît donc
sur toute pièce bureautique, y compris celles que la personne ne peut pas
écrire.

Ce n'est pas une faille : le contrôleur revérifie le droit d'écriture et
retombe en lecture seule. C'est un bouton qui promet ce qu'il ne tiendra pas.
Le correctif précharge les droits en un seul appel et rend la vérification
synchrone.

2. ``IsAdminUser`` n'est plus vrai pour tout le monde
------------------------------------------------------

Amont pose ``IsAdminUser: True`` en dur dans la réponse WOPI, pour n'importe
quel usager. Le correctif y met la vraie valeur.

3. La découverte n'est plus retéléchargée à chaque ouverture
--------------------------------------------------------------

Amont refait un appel HTTP synchrone vers ``/hosting/discovery`` du serveur
Collabora à chaque ouverture de document. Le correctif garde la réponse en
mémoire pendant une durée réglable.

Le cache porte une durée de vie plutôt qu'un vidage manuel parce que l'adresse
de l'éditeur contient le numéro de compilation de Collabora : après une mise à
niveau du serveur, une entrée gardée indéfiniment pointerait vers un chemin
disparu.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["collabora_odoo"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_collabora_online/static/src/js/collabora_patch.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
