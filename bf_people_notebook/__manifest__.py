# -*- coding: utf-8 -*-
{
    "name": "People I've Met",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "A private notebook of the people you meet: where, what they like, "
               "the detail that lets you reconnect, without making them a contact",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    # The Notebook carries the capture (a note on the phone, offline, dictated) and the
    # link to the card; this module only adds what it takes to find people again.
    "depends": ["base", "mail", "bf_bloc_notes"],
    "description": """
People I've Met
===============

A private notebook of the people you cross paths with, on a trip or closer to home:
where you met, what they like, what they know about you, and the detail that lets
you pick the conversation up again. A card is not a contact: nothing reaches the
address book until you decide so.

* **A card needs one clue only**: a first name (marked *not sure* if need be), a
  description that brings them back to mind, or a photo. Everything else is optional.
* **Where you met**: the place, the city and country, the occasion (a trip, an
  event), the date (exact, or just the month or the year) and who introduced you.
* **About them**: a circle (family, friend, travel, work, acquaintance), interests
  as tags, what they like, what they know about you, how to reconnect.
* **Your notes stay notes**: a note from the Notebook is linked to the card, and the
  latest one tells when you last saw them. *Make a person card* turns a note into a
  card in one click.
* **One search box** finds a card by anything you remember, notes included.
* **Make a contact** when the time comes. **Remind me to reconnect** is off unless
  you ask for it.

Who sees what
-------------
A card is **private to the person who wrote it**, with its photo, attachments,
thread, followers and activities. Its owner may share a card, read-only, with
chosen members of the household. There is no exception for administrators in the
access rules; the promise does not hold against a system administrator, who can
change those rules themselves.
""",
    "data": [
        "security/ir.model.access.csv",
        "security/people_rules.xml",
        "data/mail_activity_type_data.xml",
        "views/people_person_views.xml",
        "views/people_occasion_views.xml",
        "views/people_interest_views.xml",
        "views/bf_note_views.xml",
        "views/people_menus.xml",
    ],
}
