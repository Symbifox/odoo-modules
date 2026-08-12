{
    "name": "Cadre de confidentialité — GDPR (UE)",
    "version": '18.0.1.0.1',
    "category": "Privacy/Compliance",
    "summary": "Pack de cadre réglementaire GDPR (Union européenne) pour le module Vie privée",
    "description": """
Cadre réglementaire GDPR (Union européenne)
===========================================

Pack de données pour le module ``privacy_consent`` (Vie privée). Ajoute le cadre
réglementaire **GDPR / RGPD** : autorité de contrôle, délégué à la protection des
données (DPO), 6 bases légales (art. 6), droits des personnes concernées
(accès, rectification, effacement, limitation, portabilité, opposition, plainte),
seuils de notification d'incident (72 h) et citations statutaires.

Une fois installé, sélectionnez ce cadre par défaut sur la société, ou par
enregistrement (consentement, avis, calendrier de conservation), pour faire le
rendu des courriels et certificats selon le GDPR.
    """,
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": 'Other proprietary',
    "depends": ["privacy_consent"],
    "data": [
        "data/gdpr_framework_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
