{
    "name": "Cadre de confidentialité — LPRPDE / PIPEDA (Canada)",
    "version": '18.0.1.0.1',
    "category": "Privacy/Compliance",
    "summary": "Pack de cadre réglementaire LPRPDE / PIPEDA (fédéral) pour le module Vie privée",
    "description": """
Cadre réglementaire LPRPDE / PIPEDA (Canada — fédéral)
======================================================

Pack de données pour le module ``privacy_consent`` (Vie privée). Ajoute le cadre
**LPRPDE / PIPEDA** : Commissariat à la protection de la vie privée du Canada
(CPVP/OPC), responsable de la vie privée, principes d'équité de l'information,
droits d'accès et de correction, seuil de notification d'incident (« risque réel
de préjudice grave ») et citations. Pour les clients canadiens hors Québec.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": 'Other proprietary',
    "depends": ["privacy_consent"],
    "data": [
        "data/pipeda_framework_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
