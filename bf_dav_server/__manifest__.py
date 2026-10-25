# -*- coding: utf-8 -*-
{
    "name": "Calendar and contacts sync (CalDAV, CardDAV)",
    "version": "18.0.1.2.0",
    "category": "Productivity/Calendar",
    "summary": "Thunderbird, Apple Calendar and Contacts, and Android (DAVx5) read and write "
               "the Odoo agenda and contacts directly, reminders and their state included",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["calendar", "contacts", "mail"],
    "external_dependencies": {"python": ["vobject", "lxml"]},
    "data": [
        "security/ir.model.access.csv",
        "security/dav_security.xml",
        "data/mail_activity_type.xml",
        "data/ir_cron.xml",
        "wizard/dav_password_wizard_views.xml",
        "views/dav_calendar_views.xml",
        "views/dav_addressbook_views.xml",
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_dav_server/static/src/js/calendar_filter_patch.js",
            "bf_dav_server/static/src/scss/calendar_filter.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
}
