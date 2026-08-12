{
    "name": "Cadre de confidentialité — Privacy Act 2020 (Nouvelle-Zélande)",
    "version": '18.0.1.0.1',
    "category": "Privacy/Compliance",
    "summary": "Pack de cadre réglementaire NZ Privacy Act 2020 pour le module Vie privée",
    "description": """
Cadre réglementaire Privacy Act 2020 (Nouvelle-Zélande)
=======================================================

Pack de données pour le module ``privacy_consent`` (Vie privée). Ajoute le cadre
**NZ Privacy Act 2020** : Office of the Privacy Commissioner (OPC), Privacy
Officer, principes de confidentialité (IPP), droits d'accès et de correction,
schéma de notification d'incident (NotifyUs, « serious harm ») et citations.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": 'Other proprietary',
    "depends": ["privacy_consent"],
    "data": [
        "data/nz_framework_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
