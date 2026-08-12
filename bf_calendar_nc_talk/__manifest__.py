{
    "name": "BF Calendar — Nextcloud Talk button",
    "summary": "Adds a '+ Nextcloud Talk' button next to '+ Odoo meeting' on "
               "calendar events. Creates a public Talk conversation via the "
               "Spreed OCS API and writes the room URL into videocall_location.",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
    ],
    "data": [
        "views/res_config_settings_views.xml",
        "views/calendar_event_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
