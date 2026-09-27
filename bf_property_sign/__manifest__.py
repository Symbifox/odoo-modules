{
    "name": "Copropriété : signature électronique",
    "summary": "Faire signer la déclaration du carnet, l'attestation de l'art. 1068.1 et les procès-verbaux",
    "version": "18.0.1.2.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/log_signature_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont auto-installable : il s'installe seul quand la suite et bf_sign
    # sont là, et la suite fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Copropriété : signature électronique
====================================

Le pont entre la suite de copropriété et bf_sign. Il branche la signature sur
trois pièces, et ces trois-là n'ont pas le même statut : le module le dit plutôt
que de les traiter pareil.

🔴 **La déclaration d'examen sur place du carnet (r. 8.01, art. 6).** C'est la
seule pièce de la suite dont le texte exige expressément une SIGNATURE. Une case
à cocher ferait affirmer à un gestionnaire que le professionnel a déclaré, alors
que le règlement veut que le professionnel déclare lui-même. Il signe donc, et
la signature écrit la case et sa date. Il n'a
pas de compte dans l'instance et n'en a pas besoin.

⚠️ **L'attestation de l'art. 1068.1.** Le syndicat atteste, signe, et un notaire
verse la pièce au dossier de la vente. La signature électronique y remplace une
impression signée à la main.

⚠️ **Les procès-verbaux (art. 1102.1 et 1086.1).** Les deux articles exigent la
TRANSMISSION, pas la signature. Qu'un président et un secrétaire signent est un
usage répandu, pas une obligation sourcée : le pont l'offre, et le module ne
laisse jamais entendre que le procès-verbal doit être signé pour valoir.

⚠️ **Le module ne devine jamais qui signe pour le syndicat.** La suite refuse
de modéliser la composition du conseil : la déclaration de
copropriété la fixe et elle varie. Le pont ne propose donc de signataire par
défaut que là où la personne est une donnée du dossier : l'auteur du carnet. Sur
les deux autres pièces, le syndicat nomme son signataire au moment de l'envoi.
""",
    "depends": [
        "bf_property_records",
        "bf_property_governance",
        "bf_sign",
    ],
    "data": [
        "security/bf_property_sign_security.xml",
        "views/bf_property_sign_views.xml",
    ],
}
