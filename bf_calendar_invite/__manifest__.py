{
    "name": "BF Calendar — usable invitations",
    "summary": "Branded calendar invitations written in the guests' language, "
               "carrying the .ics and a link to the attendee's invitation page, "
               "plus a prefilled SMS body, a meeting status that survives a "
               "round trip through CalDAV, and a one-click poke for a guest "
               "who has not shown up.",
    # 18.0.3.0.0: the calendar view stops sending people to "More Options".
    #   Status (tentative / confirmed / cancelled, mapped to the ICS STATUS
    #   property), tags and reminders are set on the quick-create dialog
    #   itself; the status is also settable from the event popover, next to
    #   core's Yes/No/Maybe group. Adds a POKE button that opens a short
    #   "are we still meeting?" draft in the guest's language. Task #25173.
    "version": "18.0.3.1.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
        "calendar_sms",
        # Brand colours and the dark-background logo the message shell uses.
        "bf_onboarding_base",
    ],
    "data": [
        "data/report_ics.xml",
        "data/mail_body.xml",
        "data/mail_template.xml",
        "views/calendar_event_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_calendar_invite/static/src/scss/calendar_status.scss",
            "bf_calendar_invite/static/src/js/calendar_status_popover.js",
            "bf_calendar_invite/static/src/xml/calendar_status_popover.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
