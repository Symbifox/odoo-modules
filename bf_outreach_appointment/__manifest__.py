# -*- coding: utf-8 -*-
{
    "name": "Démarchage — rendez-vous",
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Un rendez-vous pris fait avancer la cible de démarchage toute seule",
    "description": """
Démarchage — rendez-vous
========================

Passerelle entre « Campagnes de démarchage » et « Rendez-vous ».

Quand une réservation (`resource.booking`) passe à « planifiée » ou
« confirmée » pour le contact d'une cible, le module :

* journalise une interaction « Rencontre » sur la cible ;
* bascule la cible à l'étape « Rendez-vous fixé ».

La campagne peut aussi porter un type de rendez-vous : son lien public devient
disponible sur chaque cible (`booking_url`), prêt à être glissé dans le modèle
de courriel de la campagne.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["bf_outreach", "bf_appointment"],
    "data": [
        "views/outreach_campaign_views.xml",
    ],
    "installable": True,
    "auto_install": True,
}
