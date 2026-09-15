# -*- coding: utf-8 -*-
{
    "name": "Babillard : accueil",
    "summary": "Ce qui reste à lire apparaît sur l'écran d'accueil",
    "version": "18.0.1.0.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "bf_home"],
    "auto_install": True,
    "installable": True,
    "assets": {
        "web.assets_backend": [
            "bf_babillard_home/static/src/xml/tuile.xml",
        ],
    },
    "description": """
Pont Babillard ↔ Accueil
========================

S'auto-installe quand `bf_babillard` et `bf_home` sont tous deux là. Une tuile
dit combien d'annonces à lecture obligatoire attendent encore la confirmation de
la personne qui regarde.

La tuile compte ce qui M'attend, jamais ce qui attend les autres : pas de
palmarès des retardataires. Le « qui n'a pas lu » existe, mais il vit dans la
publication, pour la rédaction, et il sert à relancer.
""",
}
