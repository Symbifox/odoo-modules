{
    "name": "Symbifox Appointment — Google Meet",
    "summary": "Adds Google Meet as a videoconference provider on appointment "
               "types. The Meet is attached to the host's own Google Calendar "
               "event; Nextcloud Talk is the fallback.",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        # The appointment types, bookings and the Talk fallback.
        "bf_appointment",
        # Pushes the booking's event to the host's Google Calendar, and holds
        # the host's Google OAuth token.
        "calendar_nextcloud_sync",
    ],
    "data": [],
    "installable": True,
    "application": False,
    # Installed on purpose, tenant by tenant: an auto-installed bridge would
    # appear wherever both halves happen to be. Nothing changes for existing
    # types until the new choice is picked on one.
    "auto_install": False,
}
