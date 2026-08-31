{
    "name": "BF Calendar — usable invitations",
    "summary": "Branded calendar invitations written in the guests' language, "
               "carrying the .ics and a link to the attendee's invitation page, "
               "plus a prefilled SMS body.",
    "version": "18.0.2.0.0",
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
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
