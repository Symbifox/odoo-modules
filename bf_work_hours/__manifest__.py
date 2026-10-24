{
    "name": "BF Working Hours",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "summary": "Each person's real working time, reconstructed from their own traces with an uncertainty band, "
               "seen by the person and the colleagues they name; off until the person turns it on",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["hr", "calendar", "bus"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "wizard/activate_wizard_views.xml",
        "views/work_day_views.xml",
        "views/work_share_views.xml",
        "views/res_users_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_work_hours/static/src/heartbeat_service.js",
            "bf_work_hours/static/src/consent/consent_screen.js",
            "bf_work_hours/static/src/consent/consent_screen.xml",
            "bf_work_hours/static/src/consent/consent_screen.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
