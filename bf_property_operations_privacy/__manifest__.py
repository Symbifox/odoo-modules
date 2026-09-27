{
    "name": "Exploitation : pont vie privée (Loi 25)",
    "summary": "Inscrire au registre des traitements ce qu'un quart de travail dit des salariés",
    "version": "18.0.1.3.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/register_en.png"],
    "application": False,
    "installable": True,
    # ⚠️ Pont, sur le patron de bf_property_privacy : il s'installe SEUL quand
    # les deux côtés sont là. Déclarer la finalité du quart dans
    # bf_property_privacy l'aurait inscrite au registre même là où aucun quart
    # n'existe — un registre qui annonce un traitement qui n'a pas lieu est
    # aussi faux qu'un registre qui en tait un.
    "auto_install": True,
    "description": """
Exploitation : pont vie privée (Loi 25)
=======================================

Un quart de travail nomme des personnes et enregistre ce qu'elles ont fait. Ce
sont des renseignements personnels, et la Loi 25 ne fait pas d'exception parce
que ces personnes sont des salariés.

⚠️ **Sa base n'est ni le consentement, ni une obligation du C.c.Q.** Les trois
finalités déjà au registre de la suite reposent, l'une sur l'art. 1070 al. 1
(le registre du syndicat), l'autre sur un consentement exprès (les avis par
texto), la troisième sur l'information de tiers qui n'ont rien consenti (le
journal des colis et des visiteurs). Celle-ci repose sur la **relation
d'emploi** : l'employeur sait qui a tenu le quart parce qu'il l'a confié.

🔴 **Et c'est pourquoi on ne demande PAS de consentement.** Demander à un
concierge de consentir à ce qu'on sache qui a fait la ronde laisserait croire
qu'il peut refuser, alors que refuser reviendrait à refuser de rendre compte
du travail. Le même raisonnement que pour le registre du syndicat, appliqué à
un autre régime : un consentement qu'on ne peut pas retirer n'est pas un
consentement, c'est un formulaire.

⚠️ **Ce que la Loi 25 laisse ici**, c'est l'obligation d'informer la personne du
traitement et de sa finalité, et celle de ne pas conserver au-delà de la
nécessité. Le module inscrit la finalité ; il ne purge rien, et il ne prétend
pas le faire.

⚠️ **Le contenu du quart n'est pas un dossier disciplinaire.** Ce qui s'y
enregistre est le travail : quelle ronde, quel billet, réglé ou passé au quart
suivant. Le module ne porte ni appréciation, ni rendement, ni présence : Odoo a
`hr_attendance` pour les heures, et le quart s'arrête au travail.
""",
    "depends": [
        "bf_property_operations",
        "bf_property_privacy",
    ],
    "data": [
        "data/privacy_purpose_data.xml",
    ],
}
