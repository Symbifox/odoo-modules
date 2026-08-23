# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Calendar Nextcloud Sync",
    "summary": "Bidirectional calendar synchronization between Odoo and Nextcloud (CalDAV) and Google Calendar (API v3/OAuth2)",
    # 18.0.2.12.0: l'heure poussée vers Nextcloud part avec SON fuseau.
    #   `build_ics` écrivait `DTSTART:…Z`. L'instant était juste, mais aucun
    #   fuseau n'accompagnait l'heure : le client titrait alors la fiche
    #   « 21:00 UTC » et reléguait l'heure réelle en seconde ligne grise.
    #   On écrit désormais `DTSTART;TZID=<zone>` avec le VTIMEZONE
    #   correspondant, sérialisé par vobject (déjà dans les dépendances
    #   d'Odoo) et mémorisé par processus. Le fuseau vient de la réservation
    #   quand il y en a une (celui dans lequel la personne a CHOISI son
    #   créneau), sinon de l'organisateur, sinon du défaut d'instance.
    #   ⚠️ Un TZID sans VTIMEZONE vaut une heure FLOTTANTE (RFC 5545 §3.2.19)
    #   et déplacerait le rendez-vous : le repli en UTC est donc conservé dès
    #   qu'on ne sait pas produire le VTIMEZONE. Le lien vers bf_appointment
    #   reste MOU (hasattr, pas d'import) — ce module tourne aussi chez des
    #   locataires sans prise de rendez-vous.
    "version": "18.0.2.12.0",
    "category": "Calendar",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "depends": [
        "calendar",
        "base_automation",
        "bf_timezone",
    ],
    "external_dependencies": {
        "python": [
            "googleapiclient",
            "google_auth_oauthlib",
        ],
    },
    "data": [
        # Security
        "security/ir.model.access.csv",
        # Views
        "views/calendar_event_views.xml",
        "views/nextcloud_sync_config_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
        "views/menu.xml",
        # Data (automated actions + cron)
        "data/ir_actions_server.xml",
        "data/nextcloud_sync_cron.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "calendar_nextcloud_sync/static/src/js/attendee_calendar_color_patch.js",
        ],
    },
}
