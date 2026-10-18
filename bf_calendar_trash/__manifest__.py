{
    "name": "BF Calendar — trash",
    "summary": "Deleted and archived meetings go to a trash, where they can be "
               "restored or deleted for good. A meeting moved to the trash "
               "leaves Nextcloud with it, and comes back there when restored.",
    # 18.0.1.0.0: the trash. Archiving and deleting a meeting both move it there
    #   (core's calendar already archived on "Delete"; forms and lists erased for
    #   good, with nothing to undo). The cancellation dialog can send the meeting
    #   to the trash in the same gesture. A meeting that lives in Nextcloud leaves
    #   it (DELETE, EXDATE or the end of the series, after a copy of the resource
    #   is kept) and the copy is put back when the meeting is restored. A meeting
    #   deleted in Nextcloud or Thunderbird lands in the trash too. Nothing is
    #   purged on its own: deleting for good is done by hand, from the trash.
    # 18.0.1.0.1: the trash fields of a private meeting of someone else are
    #   readable (they came out blank); deleting for good checks the right to
    #   delete BEFORE removing anything from Nextcloud or Google; the dates of a
    #   series sort by start within one gesture. Requires bf_calendar_invite
    #   18.0.6.2.0 or later (the series scope of the cancellation dialog).
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": [
        "calendar",
        # The Nextcloud half: CalDAV primitives, the series carrier, the pull.
        # 18.0.2.27.0 or later: an archived meeting is no longer brought back
        # by the next pull.
        "calendar_nextcloud_sync",
        # The cancellation dialog that gains "Move to trash".
        "bf_calendar_invite",
    ],
    "data": [
        "views/calendar_event_trash_views.xml",
        "views/calendar_event_cancel_views.xml",
    ],
    "post_init_hook": "_bf_trash_existing_archives",
    "installable": True,
    "application": False,
    "auto_install": False,
}
