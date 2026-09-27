{
    "name": "Locatif : pont vie privée (Loi 25)",
    "summary": "Inscrire au registre des traitements ce qu'un avis de résiliation dit du locataire",
    "version": "18.0.1.2.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core.
    "license": "Other proprietary",
    "images": ["static/description/register_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont auto-installable, sur le patron de bf_property_operations_privacy.
    # Déclarer ces finalités dans bf_property_privacy les inscrirait au registre
    # d'un syndicat qui ne loue rien : un registre qui annonce un traitement
    # qui n'a pas lieu est aussi faux qu'un registre qui en tait un.
    "auto_install": True,
    "depends": [
        "bf_rental_notice",
        "bf_property_privacy",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
    ],
    "description": """
Locatif : pont vie privée (Loi 25)
==================================

Le volet locatif collecte deux choses que le registre des traitements doit
nommer, et elles ne se ressemblent pas.

**Le bail et les avis** portent le nom, l'adresse, le courriel et le loyer d'une
personne. C'est ordinaire, et c'est la tenue même du dossier locatif.

🔴 **Le fondement d'une résiliation de l'art. 1974.1 est d'une autre nature.**
Il révèle qu'une personne est victime de violence sexuelle, conjugale, ou de
violence envers un enfant qui habite le logement. Sa divulgation ne cause pas
un désagrément : elle met une personne en danger.

Ce que le module a déjà fait, et que le registre documente
-----------------------------------------------------------

La lecture du fondement est réservée à la gestion, avec les deux champs qui le
trahiraient indirectement. Le champ n'est pas suivi au fil de discussion, pour
qu'un changement ne parte pas par courriel à des abonnés dont la personne ignore
la composition. La date de résiliation, elle, reste lisible de tous : c'est le
*pourquoi* qui est réservé, pas le *quand*.

⚠️ Ce que ce module N'affirme pas
----------------------------------

La **base juridique** de ce traitement est portée au registre comme une
obligation légale : le locateur reçoit l'avis et l'attestation parce que le Code
civil organise ainsi l'exercice d'un droit du locataire. Cette qualification
**n'a pas été validée par un conseiller juridique** au moment d'écrire ces
lignes.

Un registre qui tait un traitement est plus faux qu'un registre dont une base
reste à préciser : c'est pourquoi la finalité est inscrite plutôt que gardée en
attente. Le texte en langage clair dit ce qui est collecté et ce qui en est
fait, ce qui est l'obligation la plus concrète de la Loi 25 ; la qualification
de la base se corrigera si un avis juridique la contredit.
""",
}
