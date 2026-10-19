{
    "name": "BF Time of Day",
    # 18.0.1.4.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.4.1: la plage horaire quitte la rangée en ligne de l'échéance
    #   pour une rangée à elle : partagée à parts égales, elle rognait la
    #   date limite.
    # 18.0.1.5.0: la plage horaire prend une couleur libre (bf_color) ;
    #   la pastille de la carte de tâche la reprend telle quelle.
    # 18.0.1.5.1: la couleur affichée, dans la liste et la fiche, ouvre « Ma couleur ».
    # 18.0.1.5.2: le recalage sur la plage lit le fuseau du contexte avant
    #   celui de l'usager. La fédération écrit le jour reçu au nom du fuseau
    #   de la société ; le superutilisateur, sans fuseau, recalait en UTC et
    #   l'échéance glissait d'un jour pour qui est loin d'UTC.
    "version": "18.0.1.5.2",
    "category": "Project",
    "summary": "Plages horaires (Matinée / Midi / Fin de jour / Hors heures) pour tâches et activités",
    'author': 'Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": ["bf_color", "project", "mail", "bf_onboarding_base"],
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
