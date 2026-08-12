{
    "name": "BF Time of Day",
    "version": "18.0.1.3.1",
    "category": "Project",
    "summary": "Plages horaires (Matinée / Midi / Fin de jour / Hors heures) pour tâches et activités",
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["project", "mail", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_time_of_day_security.xml",
        "data/bf_time_of_day_data.xml",
        "data/bf_time_of_day_filters.xml",
        "data/bf_onboarding.xml",
        "views/bf_time_of_day_views.xml",
        "views/res_users_views.xml",
        "views/project_task_views.xml",
        "views/mail_activity_views.xml",
        "views/menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_time_of_day/static/src/scss/kanban_badge.scss",
        ],
    },
    "installable": True,
    "application": False,
}
