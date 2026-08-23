{
    "name": "Progression — Échéancier Gantt",
    "summary": "Visualisation Gantt (niveau tâche, regroupée par étape de "
               "progression) des mandats d'accompagnement, en complément de "
               "Step-by-Step.",
    "description": """
Échéancier Gantt de la progression des projets
==============================================

Composant OWL maison (Odoo Community — pas de vue Gantt native) qui affiche les
tâches d'un projet sous forme de barres temporelles, regroupées par *étape de
progression* (les mêmes étapes que le module ``bf_stepbystep_clients``).

* Barres dérivées des dates existantes (lecture seule) : début =
  ``date_assign``/``create_date``, fin = ``date_deadline``.
* Remplissage = avancement (heures réalisées / allouées) ou état de la tâche.
* Couleurs de statut : terminé · annulé · en retard · en cours · à venir.
* Flèches de dépendances (``depend_on_ids``).
* Zoom Jour / Semaine / Mois, ligne « aujourd'hui », tooltips, clic → fiche tâche.

Accessible depuis l'app *Progression*, l'app *Projets* et un bouton sur la fiche
projet.
""",
    "version": "18.0.1.0.0",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "bf_stepbystep_clients",
        "project",
        "hr_timesheet",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/gantt_action.xml",
        "views/gantt_menu.xml",
        "views/project_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_progression_gantt/static/src/js/bf_progression_gantt.js",
            "bf_progression_gantt/static/src/xml/bf_progression_gantt.xml",
            "bf_progression_gantt/static/src/scss/bf_progression_gantt.scss",
        ],
    },
}
