# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Calendar Nextcloud Sync",
    "summary": "Bidirectional calendar synchronization between Odoo and Nextcloud (CalDAV) and Google Calendar (API v3/OAuth2)",
    "version": "18.0.2.11.0",
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
