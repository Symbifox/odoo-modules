# -*- coding: utf-8 -*-
{
    "name": "Plans d'étage : hébergement",
    "summary": "Les appareils et les serveurs de la gestion d'hébergement, "
               "posés sur le plan d'étage",
    "version": "18.0.1.0.0",
    "category": "Services",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Plans d'étage : hébergement
===========================

Pont entre « Plans d'étage » et « Gestion d'hébergement ». Un élément du
plan peut représenter un appareil du parc (`hosting.endpoint`) ou un serveur
(`hosting.server`) :

* cliquer la forme ouvre la fiche de l'appareil, pas celle de l'élément ;
* le plan colore ce qui demande attention : système en fin de vie, appareil
  en réparation ou retiré, serveur décommissionné (rouge) ; garantie échue,
  serveur en maintenance (ambre) ;
* l'infobulle dit qui a l'appareil, son système et son état ;
* depuis la fiche de l'appareil ou du serveur, « Sur le plan » mène à sa
  place, ou propose de la choisir s'il n'en a pas encore.

Un appareil n'est posé qu'à un seul endroit. Le plan ne stocke aucun fait
sur l'appareil : il lit la fiche, et la fiche reste la vérité.
""",
    "depends": ["bf_floorplan", "hosting_management"],
    "data": [
        "views/bf_floorplan_element_views.xml",
        "views/hosting_views.xml",
    ],
}
