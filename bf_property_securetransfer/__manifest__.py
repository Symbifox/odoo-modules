{
    "name": "Copropriété : remise sécurisée des documents",
    "summary": "Remettre l'attestation, l'état des charges et les documents à l'acquéreur par transfert sécurisé, et savoir s'ils ont été lus",
    "version": "18.0.2.4.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core.
    "license": "Other proprietary",
    "images": ["static/description/attestation_transfer_en.png"],
    "application": False,
    "installable": True,
    # Pont, sur le patron de bf_cx : il s'installe seul quand les deux côtés
    # sont là, et la suite fonctionne parfaitement sans lui.
    "auto_install": True,
    "description": """
Copropriété : remise sécurisée des documents
============================================

Les trois régimes de documents à l'acquéreur SONT une transmission de pièces à
un tiers, et deux d'entre eux portent un délai de quinze jours. C'est
exactement ce que le transfert sécurisé sait faire.

⚠️ **Le délai de l'art. 1069 al. 2 joue CONTRE le syndicat**, seul cas de la
suite. Passé quinze jours, l'acquéreur n'est plus tenu des charges dues et la
créance se réclame au vendeur, souvent parti. La date de remise n'est donc pas
une information de suivi, c'est ce qui arrête l'horloge.

⚠️ **Une remise n'est pas une lecture.** Un lien sécurisé expire. S'il expire
sans que personne ne l'ait ouvert, le syndicat a envoyé mais l'acquéreur n'a
rien reçu. Le module le dit, plutôt que de compter une remise qui n'a pas eu
lieu.

⚠️ **La copie remise est gelée sur la fiche quand le module la produit.** Le transfert sécurisé efface sa
propre copie après l'envoi, et il a raison : il ne doit pas garder les octets
d'un tiers dans son dépôt. Mais le syndicat, lui, doit pouvoir dire ce qu'il a
remis, et une attestation régénérée six mois plus tard ne dirait pas la même
chose : elle est datée de sa remise et reflète l'état de ce jour-là. Les deux
copies ont des raisons d'être différentes. Les régimes qui remettent des pièces
JOINTES par le syndicat ne gèlent rien : la pièce jointe est déjà la copie de
la fiche.

⚠️ **L'art. 1068.2 se refuse tant que la revue de vie privée n'est pas faite.**
L'autorisation du promettant acheteur ne couvre pas les renseignements
personnels des autres copropriétaires, et le caviardage se décide avant la
remise, pas après.
""",
    "depends": [
        "bf_property_records",
        # ⚠️ L'état des charges de l'art. 1069 al. 2 vit dans le module
        # financier, et c'est celui dont le délai joue contre le syndicat :
        # le pont ne serait pas un pont sans lui.
        "bf_property_finance",
        "bf_securetransfer",
    ],
    "data": [
        "security/bf_property_securetransfer_security.xml",
        "views/bf_property_securetransfer_views.xml",
    ],
}
