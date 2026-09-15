# -*- coding: utf-8 -*-
{
    "name": "Babillard : célébrations",
    "summary": "Une carte de fête livrée se voit au babillard",
    "version": "18.0.1.0.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "bf_celebrations"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Célébrations
=============================

S'auto-installe quand `bf_babillard` et `bf_celebrations` sont tous deux là.
Quand une carte collective part vers la personne fêtée, une carte paraît au
babillard pour que le bureau la voie passer.

Le pont ne publie que ce que les célébrations rendent déjà public : le nom
affiché de la personne, qui a consenti à ce qu'on la souligne. Jamais la date de
naissance, jamais les signatures.
""",
}
