{
    "name": "Expérience client",
    "summary": "Mesure de l'expérience client : NPS, feedback continu, plaintes et témoignages",
    "version": "18.0.1.4.0",
    "post_init_hook": "post_init_hook",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "auto_install": False,
    "description": """
Expérience client
=================

Programme d'écoute client intégré à Odoo, sans licence externe :

**NPS (Net Promoter Score)**
  - Programmes de mesure appuyés sur les sondages Odoo (question échelle 0-10)
  - Vagues d'envoi par lot de contacts, avec jetons individuels et rappels
  - Calcul automatique du score (promoteurs − détracteurs) par programme et par vague

**Feedback en continu**
  - Ingestion automatique des évaluations par courriel (module « rating » :
    projets, tickets) dans un registre unifié
  - Boucle fermée : un détracteur ou une note insatisfaite crée une activité
    de suivi assignée au responsable du compte

**Gestion des plaintes**
  - Registre de plaintes avec gravité, échéance d'accusé de réception,
    analyse de cause et action corrective
  - Pont automatique vers le helpdesk quand helpdesk_mgmt est installé
    (module bf_cx_helpdesk)

**Témoignages**
  - Recueil de candidats-témoignages depuis les sondages
  - Suivi du consentement (verbal, écrit, ou via le module Vie privée /
    Loi 25 quand privacy_consent est installé - module bf_cx_privacy)

**Feedback interne (360)**
  - Programmes de type interne appuyés sur les mêmes sondages, entrées
    réservées aux gestionnaires du module
""",
    "depends": [
        "bf_onboarding_base",
        "mail",
        "survey",
        "rating",
        "utm",
        "project",
    ],
    "data": [
        # Security (groups before access CSV)
        "security/bf_cx_security.xml",
        "security/ir.model.access.csv",
        # Data
        "data/ir_sequence_data.xml",
        "data/mail_template_data.xml",
        "data/survey_template_data.xml",
        "data/survey_360_data.xml",
        "data/ir_cron_data.xml",
        # Views (actions before menus)
        "views/bf_cx_feedback_views.xml",
        "views/bf_cx_program_views.xml",
        "views/bf_cx_wave_views.xml",
        "views/bf_cx_complaint_views.xml",
        "views/bf_cx_testimonial_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
        "views/bf_cx_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_cx/static/src/js/cx_dashboard.js",
            "bf_cx/static/src/xml/cx_dashboard.xml",
            "bf_cx/static/src/scss/cx_dashboard.scss",
        ],
    },
}
