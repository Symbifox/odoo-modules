{
    "name": "BF Calendar Work Blocks",
    "summary": "Book typed blocks of time (focus, production, meeting slot, "
               "catch-up), each with its own pictogram and colour in the "
               "calendar, and carried to other calendars as a category.",
    # 18.0.1.0.0 : plages typées. Avant ce module,
    #   le type d'une plage ne tenait que dans son titre (« PROD »,
    #   « Rattrapage », « temps de concentration »), indiscernable du reste dans
    #   la grille. Un type de plage est
    #   une étiquette d'agenda (`calendar.event.type`) marquée « plage de
    #   travail », avec un pictogramme, une couleur et une disponibilité par
    #   défaut. L'étiquette est la forme ouverte et standard de typer un
    #   événement (CATEGORIES, RFC 5545) : calendar_nextcloud_sync 2.26.0 la
    #   porte vers Nextcloud et la relit, et Thunderbird, Apple ou DAVx5
    #   l'affichent. La plage de rencontre (libre par défaut) ouvre la prise
    #   de rendez-vous avec bf_calendar_block_appointment.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
        # La création rapide y porte déjà le statut et les étiquettes.
        "bf_calendar_invite",
        # La couleur libre des étiquettes (bf.color.mixin sur calendar.event.type).
        "bf_color_calendar",
        # Porte bf.color.mixin sur calendar.event, le patch de la grille qui lit
        # `color_resolved`, et CATEGORIES vers Nextcloud depuis la 2.26.0.
        "calendar_nextcloud_sync",
    ],
    "data": [
        "data/bf_color_rule.xml",
        "views/calendar_event_type_views.xml",
        "views/calendar_event_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_calendar_block/static/src/js/block_calendar.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
