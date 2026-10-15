{
    "name": "BF Calendar Subscriptions",
    "summary": "Show calendars published elsewhere (Outlook, Google, Apple) in the "
               "Odoo calendar, read only, and keep their busy times out of online booking.",
    # 18.0.1.0.0 : agendas externes abonnés (lot 6). La
    #   moitié « Symbifox voit Outlook » du lien bidirectionnel, en protocoles
    #   ouverts seulement (arbitrage du 2026-10-14) : un agenda publié
    #   en iCalendar est relu à intervalle, montré en lecture dans la grille dans
    #   sa couleur, et ses créneaux occupés ferment la prise de rendez-vous. Les
    #   événements importés sont privés par défaut, sans rappel, sans ordre du
    #   jour, et ne repartent nulle part. Lecture gardée contre les adresses
    #   internes (même garde que bf_bookmarks).
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["calendar", "bf_color"],
    "external_dependencies": {"python": ["vobject"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/bf_color_rule.xml",
        "views/views.xml",
    ],
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
