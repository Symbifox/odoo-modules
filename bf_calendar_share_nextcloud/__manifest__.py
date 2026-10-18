{
    "name": "BF Calendar Sharing - Nextcloud calendars",
    "summary": "Choose which Nextcloud calendars a calendar sharing link shows.",
    # 18.0.1.0.0 : portée des liens par agenda.
    #   Arbitrage du 2026-10-14 : chaque lien choisit les agendas qu'il
    #   montre (par exemple travail, client, agenda personnel), l'agenda marqué
    #   par défaut d'office ; les événements nés dans Odoo sans agenda Nextcloud suivent.
    #   Un pont, pour que bf_calendar_share marche aussi sans Nextcloud.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["bf_calendar_share", "calendar_nextcloud_sync"],
    "data": ["views/views.xml"],
    "installable": True,
    "application": False,
    "auto_install": True,
}
