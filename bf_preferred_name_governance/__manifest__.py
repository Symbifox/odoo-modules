# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Gouvernance",
    # 18.0.1.0.0: premier jet.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Les résolutions impriment le nom légal des signataires et des administrateurs",
    "description": """
Pont Gouvernance
================

Une résolution est un acte : ce pont, installé de lui-même, y imprime le nom
légal des personnes (``bf_preferred_name``), leur nom d'usage quand elles n'en
ont pas. Les signataires saisis, les administrateurs du conseil, le proposeur
et le secondeur, et la ligne « Administrateur(s) » du procès-verbal.

Le reste du module (fiches, messages, listes) garde le nom d'usage.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_corporate_governance"],
    "data": [
        "views/corporate_resolution_templates.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
