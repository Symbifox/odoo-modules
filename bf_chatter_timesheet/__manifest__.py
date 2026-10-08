{
    "name": "Symbifox — Feuille de temps depuis le chatter",
    "summary": "Case à cocher dans le composer du chatter pour journaliser une feuille de temps en même temps qu'une note interne (tâches + tickets helpdesk).",
    # 18.0.1.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.3.0",
    "category": "Services/Timesheets",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    'license': 'LGPL-3',
    "depends": [
        "bf_timesheet_timer",
        "mail",
        "hr_timesheet",
        "project",
        "bf_onboarding_base",
    ],
    "data": [
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_chatter_timesheet/static/src/js/*.js",
            "bf_chatter_timesheet/static/src/xml/*.xml",
            "bf_chatter_timesheet/static/src/scss/*.scss",
        ],
    },
    "installable": True,
    "application": False,
}
