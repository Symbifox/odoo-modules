{
    "name": "BF Activités - Lien Calendrier",
    # 18.0.1.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.2.0",
    "summary": "Lier des événements calendrier existants aux activités",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["calendar", "calendar_nextcloud_sync"],
    "data": [
        "views/mail_activity_schedule_views.xml",
        "views/mail_activity_views.xml",
        "views/calendar_event_views.xml",
    ],
    "installable": True,
    "auto_install": False,
}
