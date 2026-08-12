{
    "name": "Expérience client - pont Vie privée (Loi 25)",
    "summary": "Consentement formel des témoignages via le module Vie privée",
    "version": "18.0.1.1.0",
    "category": "Marketing/Customer Experience",
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Vie privée (Loi 25)
============================================

S'auto-installe quand bf_cx et privacy_consent sont tous deux
installés. Ajoute :

- une finalité de traitement « Témoignage client » et son avis de
  consentement ;
- le bouton « Demander le consentement (Loi 25) » sur un témoignage :
  crée une demande privacy.consent et l'envoie au client par courriel ;
- la vérification du consentement accordé avant publication.
""",
    "depends": [
        "bf_cx",
        "privacy_consent",
    ],
    "data": [
        "data/privacy_data.xml",
        "views/bf_cx_testimonial_views.xml",
    ],
}
