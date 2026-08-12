{
    "name": "Cadre de confidentialité — UK GDPR (Royaume-Uni)",
    "version": '18.0.1.0.1',
    "category": "Privacy/Compliance",
    "summary": "Pack de cadre réglementaire UK GDPR / DPA 2018 pour le module Vie privée",
    "description": """
Cadre réglementaire UK GDPR / Data Protection Act 2018 (Royaume-Uni)
====================================================================

Pack de données pour le module ``privacy_consent`` (Vie privée). Ajoute le cadre
**UK GDPR** : autorité de contrôle (ICO), délégué à la protection des données
(DPO), 6 bases légales, droits des personnes concernées, seuils de notification
d'incident (72 h) et citations statutaires. Variante britannique du GDPR
post-Brexit.
    """,
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": 'Other proprietary',
    "depends": ["privacy_consent"],
    "data": [
        "data/uk_framework_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
