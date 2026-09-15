# -*- coding: utf-8 -*-
{
    "name": "Babillard : événements",
    "summary": "Un événement à venir paraît au fil, avec sa date",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "event"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Événements
===========================

S'auto-installe quand `bf_babillard` et Événements sont tous deux là. Un
événement à venir paraît au babillard dès sa création, et sa carte tombe
d'elle-même le lendemain de l'événement. Un changement de nom ou de date suit ;
un événement annulé, terminé ou archivé quitte le fil.

Le babillard annonce, il ne remplace pas la rencontre. Sur le terrain,
l'assemblée en personne reste le canal le plus efficace.
""",
}
