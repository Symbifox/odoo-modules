{
    "name": "Copropriété — Portail de l'occupant",
    "summary": "Annonces, documents, coordonnées, demandes d'entretien et réservation des espaces communs",
    "version": "18.0.3.0.0",
    "category": "Services/Property",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ BUSL-1.1 — voir la note du manifeste de bf_property_core. Le fichier
    # LICENSE fait foi, et sa Change Date se retamponne à la publication.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "description": """
Copropriété — Portail de l'occupant
===================================

Ce que le syndicat rend consultable, et à qui.

⚠️ Ce module ne publie rien tout seul. L'art. 1070.1 C.c.Q. conditionne la
consultation du registre : elle se fait en présence d'un administrateur ou
d'une personne désignée, à des heures raisonnables, selon le règlement de
l'immeuble, et les copies s'obtiennent moyennant des frais raisonnables.
Déposer une pièce du registre sur un portail donne DAVANTAGE que ce que
l'article impose. C'est une décision du syndicat, jamais un effet de bord :
publier une pièce du registre exige une reconnaissance explicite, et le geste
est consigné.

⚠️ Le locataire n'est pas le copropriétaire. Il figure au registre par son nom
et son adresse (art. 1070 al. 1), ce qui ne lui ouvre aucun droit sur les
pièces financières du syndicat. L'auditoire se règle donc pièce par pièce, et
il est appliqué par les règles d'accès, pas seulement par l'affichage.
""",
    "depends": [
        "bf_property_core",
        "portal",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_property_portal_security.xml",
        "data/ir_sequence_data.xml",
        "views/bf_property_announcement_views.xml",
        "views/bf_property_request_views.xml",
        "views/bf_property_booking_views.xml",
        "views/bf_property_document_views.xml",
        "views/bf_property_syndicat_views.xml",
        "views/bf_property_portal_menus.xml",
        "views/portal_templates.xml",
    ],
}
