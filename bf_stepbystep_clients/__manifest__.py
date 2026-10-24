{
    "name": "Step-by-Step — Suivi d'accompagnement client",
    "summary": "Tableau de bord interne et portail libre-service de progression "
               "linéaire pour tout mandat d'accompagnement client.",
    # 18.0.2.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.2.4.0: le portail client que la fiche promettait. Une page
    #   publique à jeton propre au portail (pas l'access_token du projet, qui
    #   ouvre aussi les tâches par /my/projects), publiée depuis la fiche projet.
    #   Le client y dépose les pièces attendues à l'étape (bf.progression.document) ;
    #   chacune devient une pièce jointe de la tâche de l'étape (bf.progression.deposit).
    # 18.0.2.4.1: la page du portail déclarée website=True ; sur un locataire qui a
    #   le module website (la démo), elle rendait une 500 (KeyError 'website').
    # 18.0.2.4.2: « Étape 2 sur 4: » n'avait pas d'espace avant le deux-points en
    #   français ; séparateur neutre (point médian) entre le rang et le nom de l'étape.
    # 18.0.2.4.3: un mandat sans heures allouées n'a plus de budget inventé de 40 h
    #   (« Sans budget », hors des alertes) ; le tableau est réservé aux usagers des projets.
    # 18.0.2.4.5: l'activité récente du détail ne montre que les feuilles de temps que
    #   l'usager a le droit de lire (règles des feuilles de temps) ; tâches, étapes et
    #   chatter du détail, seulement les tâches qu'il peut lire (règles de société).
    "version": "18.0.2.4.5",
    "category": "Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "post_init_hook": "post_init_hook",
    "depends": [
        "base",
        "project",
        "hr_timesheet",
        "portal",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/progression_security.xml",
        "views/project_task_type_views.xml",
        "views/dashboard_views.xml",
        "views/dashboard_menu.xml",
        "views/project_project_views.xml",
        "views/progression_portal_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_stepbystep_clients/static/src/js/bf_stepbystep_dashboard.js",
            "bf_stepbystep_clients/static/src/xml/bf_stepbystep_dashboard.xml",
            "bf_stepbystep_clients/static/src/scss/dashboard.scss",
        ],
        "web.assets_frontend": [
            "bf_stepbystep_clients/static/src/scss/progression_portal.scss",
        ],
    },
}
