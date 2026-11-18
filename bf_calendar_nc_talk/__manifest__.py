{
    "name": "BF Calendar — Nextcloud Talk button",
    "summary": "Adds a '+ Nextcloud Talk' button next to '+ Odoo meeting' on "
               "calendar events. Creates a public Talk conversation via the "
               "Spreed OCS API and writes the room URL into videocall_location.",
    # 18.0.1.2.0: le module était déjà écrit en anglais, mais n'avait aucun
    #   catalogue français : un usager réglé en fr_CA le lisait en anglais.
    #   i18n/fr_CA.po et le .pot du module portent maintenant le français.
    "version": "18.0.1.2.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_calendar_nc_talk/static/src/js/nc_talk_form_controller.js",
        ],
    },
    "data": [
        "views/res_config_settings_views.xml",
        "views/calendar_event_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
