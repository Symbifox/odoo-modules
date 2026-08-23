{
    "name": "BF Client Onboarding",
    "version": "18.0.1.0.0",
    "category": "Project",
    "summary": "Onglet Progression sur project.project : NDA, intake, kickoff, activation",
    "description": """
BF Client Onboarding
====================

Ajoute un onglet « Progression » sur project.project qui agrège, en une vue
unique, l'état d'avancement d'un client à travers son cycle :

* État (statusbar) : not_started → nda_pending → nda_signed → intake_pending
  → intake_completed → kickoff_done → active (+ on_hold)
* Génération NDA via bf_letter_writer, suivi optionnel LibreSign
* Intake via survey.survey (auto-advance à la soumission)
* Kickoff lié à un meeting.record (confirmation manuelle)
* Agrégation des signaux existants : matrice de connaissances, consentements,
  dernière rencontre, solde banque d'heures
* Vue kanban cockpit groupée par état pour balayage rapide
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": [
        "project",
        "survey",
        "bf_meeting",
        "bf_hour_bank",
        "bf_letter_writer",
        "privacy_consent",
        "project_knowledge_matrix",
    ],
    "data": [
        "data/onboarding_survey.xml",
        "data/letter_template_nda.xml",
        "data/ir_cron.xml",
        "views/project_views.xml",
        "views/project_kanban_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
