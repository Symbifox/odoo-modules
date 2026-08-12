{
    'name': 'Rencontres',
    'version': '18.0.3.44.0',
    'category': 'Services/Meetings',
    'summary': 'Gestion des rencontres, ordres du jour et comptes rendus',
    'description': """
Rencontres / Meetings
=====================

Application complète de gestion des rencontres couvrant le cycle complet :
ordre du jour, événement calendrier, compte rendu, tâches et matrice de
connaissances.

Fonctionnalités principales
---------------------------
* Ordres du jour (agenda) avec sujets planifiés et envoi par courriel
* Comptes rendus structurés (participants, décisions, sujets, verbatim)
* Suivi des présences (présent, absent, excusé)
* Lien avec les événements de calendrier Odoo et les projets
* Lien bidirectionnel avec les matrices de connaissances
* Notes structurées JSON + HTML calculé (rendu sécurisé)
* Rapport PDF brandé, envoi d'ordre du jour et de compte rendu par courriel
* Vues kanban, calendrier, liste et formulaire
* Smart buttons sur les projets et les événements calendrier
* Suivi complet via chatter et activités

Unification OdJ et compte rendu
-------------------------------
Un même ``calendar.event`` peut porter un ordre du jour et un compte rendu :
la création d'un compte rendu depuis un événement déjà rattaché à un OdJ lie
automatiquement les deux et propage le projet. Le champ calculé
``bf_needs_agenda`` sur ``calendar.event`` signale les rencontres à venir
sans OdJ ; une case ``bf_skip_agenda`` permet de dispenser les rencontres
internes courtes ou récurrentes. Un cron quotidien crée une activité « À
faire » sur l'organisateur si l'OdJ n'a pas été envoyé et que la rencontre
arrive dans les 7 jours.

Tâches à discuter en rencontre
------------------------------
Une tâche ``project.task`` peut être rattachée à une rencontre à venir selon
quatre modes :

* **Épinglée** : liée à un ordre du jour précis (hard link).
* **Prochaine rencontre client** : apparaît au prochain OdJ admissible du client.
* **Prochaine rencontre projet** : apparaît au prochain OdJ admissible du projet.
* **Toutes les rencontres client/projet** : apparaît à tous les OdJ admissibles
  tant que la tâche reste ouverte.

Les tâches sont résolues dynamiquement à l'ouverture de l'ordre du jour, et
disparaissent automatiquement lorsqu'elles sont fermées ou transférées au
compte rendu.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    'depends': ['project', 'mail', 'calendar', 'project_knowledge_matrix', 'bf_onboarding_base', 'bf_timezone'],
    'data': [
        'security/meeting_security.xml',
        'security/ir.model.access.csv',
        'report/meeting_report_paperformat.xml',
        'report/meeting_agenda_report_templates.xml',
        'report/meeting_report_templates.xml',
        'data/meeting_report_mail_template.xml',
        'data/meeting_agenda_mail_template.xml',
        'data/meeting_agenda_cron.xml',
        'data/meeting_dashboard_cron.xml',
        'data/bf_onboarding.xml',
        'views/meeting_decision_views.xml',
        'views/meeting_topic_views.xml',
        'views/meeting_record_views.xml',
        'views/meeting_refine_wizard_views.xml',
        'views/meeting_agenda_views.xml',
        'views/agenda_contrib_templates.xml',
        'views/meeting_attendance_views.xml',
        'views/calendar_event_views.xml',
        'views/project_task_views.xml',
        'views/project_views.xml',
        'views/res_company_views.xml',
        'views/res_users_views.xml',
        'views/menu_views.xml',
        'views/meeting_dashboard_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'assets': {
        'web.assets_backend': [
            'bf_meeting/static/src/scss/meeting_dashboard.scss',
            'bf_meeting/static/src/js/meeting_dashboard.js',
            'bf_meeting/static/src/xml/meeting_dashboard.xml',
        ],
    },
}
