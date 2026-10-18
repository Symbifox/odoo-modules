# -*- coding: utf-8 -*-
{
    "name": "Démarchage — rendez-vous",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.1.1: qui n'a pas les droits Démarchage pouvait ne plus créer ni
    #   déplacer un rendez-vous : la recherche des cibles tournait avec ses
    #   droits, hors du `try`. Rapprochement en sudo, entièrement sous le `try`,
    #   un point de sauvegarde par essai ; la règle de société remise dans le
    #   domaine (société du type, à défaut les sociétés actives).
    "version": "18.0.1.1.1",
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
    "license": "Other proprietary",
    "depends": ["bf_outreach", "bf_appointment"],
    "data": [
        "views/outreach_campaign_views.xml",
    ],
    "installable": True,
    "auto_install": True,
}
