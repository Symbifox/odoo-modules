# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Reçus de dons",
    # 18.0.1.0.0: premier jet.
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "summary": "Le reçu officiel de don porte le nom légal du donateur",
    "description": """
Pont Reçus de dons
==================

Le reçu officiel de don aux fins de l'impôt est un acte : l'Agence du revenu
du Canada l'attend au nom légal du donateur. Ce pont, installé de lui-même
avec ``bf_receipt_ca``, y imprime le nom légal (``bf_preferred_name``), le nom
d'usage quand le donateur n'en a pas. Le reste (fiche, courriels) garde le nom
d'usage.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_receipt_ca"],
    "data": [
        "views/bf_receipt_ca_templates.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
