{
    "name": "Expérience client - pont Helpdesk",
    "summary": "Crée des tickets helpdesk depuis les plaintes et les détracteurs",
    "version": "18.0.1.1.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Helpdesk
=================================

S'auto-installe quand bf_cx et helpdesk_mgmt sont tous deux
installés. Ajoute :

- une équipe helpdesk « Plaintes » et un canal « Expérience client » ;
- la création d'un ticket depuis une plainte (lien bidirectionnel) ;
- la création d'un ticket de suivi depuis un feedback détracteur ;
- l'option de boucle fermée « ticket automatique » (paramètre
  bf_cx.auto_ticket, désactivée par défaut).
""",
    "depends": [
        "bf_cx",
        "helpdesk_mgmt",
    ],
    "data": [
        "data/helpdesk_data.xml",
        "views/bf_cx_complaint_views.xml",
        "views/bf_cx_feedback_views.xml",
        "views/res_config_settings_views.xml",
    ],
}
