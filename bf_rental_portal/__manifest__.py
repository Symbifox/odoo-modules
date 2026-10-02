{
    "name": "Locatif : portail du locataire",
    "summary": "Son bail, ses avis et leurs échéances, ce qu'il a payé",
    "version": "18.0.1.8.1",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "images": ["static/description/tenant_home_en.png"],
    "application": False,
    "installable": True,
    "auto_install": False,
    # ⚠️ Pas de dépendance à `bf_property_portal`, et c'est un choix.
    # Le portail de la copropriété s'adresse aux copropriétaires et aux
    # occupants d'un syndicat ; celui-ci s'adresse aux locataires d'un bailleur.
    # Un propriétaire d'immeubles locatifs n'a pas de syndicat, et lui imposer
    # les annonces, les réservations d'espaces communs et le registre de
    # l'art. 1070 reviendrait à lui installer des obligations qui ne sont pas
    # les siennes. Les deux portails cohabitent quand les deux sont installés ;
    # aucun n'a besoin de l'autre.
    "depends": [
        "bf_rental_notice",
        "bf_rental_rent",
        "portal",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_rental_portal_security.xml",
        "views/portal_templates.xml",
        "data/mail_template_invitation.xml",
        "views/bf_rental_lease_views.xml",
    ],
    "description": """
Locatif : portail du locataire
==============================

Le locataire retrouve son bail, ses avis et ce qu'il a payé, sans avoir à
téléphoner. Trois pages, en lecture seule.

🔴 L'appartenance se lit au BAIL, jamais à la fraction
-------------------------------------------------------

Le portail de la copropriété résout son auditoire par l'occupant inscrit à la
fraction, au titre du registre de l'art. 1070 C.c.Q. Réutiliser cette porte ici
se tromperait trois fois : les co-locataires d'un même bail disparaîtraient, les
baux de chambre et de terrain de maison mobile aussi (ils ne correspondent à
aucune fraction), et surtout, une personne inscrite à la fraction sans être
partie au bail y accéderait. C'est le bail qui dit qui est locataire.

Ce que les pages refusent de dire
----------------------------------

**Elles n'annoncent jamais une éviction.** Seul le tribunal résilie
(art. 1971 : le locateur « peut obtenir »), le seuil de trois semaines ne touche
qu'à la marge de manœuvre du tribunal (art. 1973), et payer avant jugement
arrête tout (art. 1883). Un écran qui ferait peur dirait trois faussetés.

**Elles n'affichent pas le fondement d'une résiliation de l'art. 1974.1.**
Violence sexuelle, violence conjugale, violence envers un enfant :
le champ est réservé à la gestion, donc hors de portée du portail. Il n'aurait
de toute façon rien à faire sur un écran qu'on consulte dans une cuisine,
devant qui que ce soit.

**Elles ne totalisent pas le loyer qui reste à courir.** L'art. 1905 rend sans
effet la clause qui rendrait le loyer entier exigible ; l'afficher réclamerait
par l'écran ce qu'on ne peut pas réclamer en droit. Ce qui s'affiche, c'est le
solde terme par terme.

Ce qu'elles disent, et qui coûte cher à ignorer
------------------------------------------------

**La date limite de réponse à un avis, et ce que le silence produira.** Le
silence vaut acceptation sur une modification du bail (art. 1945) et refus sur
une reprise ou une éviction (art. 1962). Un locataire qui laisse passer le mois
sur un avis de modification voit son bail reconduit avec tout ce qui a été
demandé. C'est l'information la plus chère du corpus, et elle n'est nulle part
ailleurs.
""",
}
