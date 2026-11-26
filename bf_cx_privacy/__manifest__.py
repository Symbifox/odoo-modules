{
    "name": "Expérience client - pont Vie privée (Loi 25)",
    "summary": "Consentement formel des témoignages via le module Vie privée",
    # 18.0.1.1.1: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français ; la trace d'un consentement perdu s'écrit
    #   dans la langue de la société et nomme le statut au lieu de sa clé.
    "version": "18.0.1.1.1",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
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
