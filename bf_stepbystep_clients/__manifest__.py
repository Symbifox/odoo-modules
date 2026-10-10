{
    "name": "Step-by-Step — Suivi d'accompagnement client",
    "summary": "Tableau de bord interne et portail libre-service de progression "
               "linéaire pour tout mandat d'accompagnement client.",
    # 18.0.2.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.2.3.0",
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
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/project_task_type_views.xml",
        "views/dashboard_views.xml",
        "views/dashboard_menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_stepbystep_clients/static/src/js/bf_stepbystep_dashboard.js",
            "bf_stepbystep_clients/static/src/xml/bf_stepbystep_dashboard.xml",
            "bf_stepbystep_clients/static/src/scss/dashboard.scss",
        ],
    },
}
