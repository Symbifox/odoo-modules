{
    "name": "Symbifox École : rencontres de parents",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Parent-teacher meetings: the office sets each teacher's hours, parents book "
               "a slot with each teacher of their child, without overlaps",
    "description": """
Parent-teacher meetings
=======================

- one session per report card (or any occasion), for chosen groups, with a
  slot length, a place and a booking period;
- the office enters each teacher's hours; the slots are generated from them
  (booked slots are never touched when regenerating);
- on the family portal, each child shows the teachers of their groups and
  their free slots: one click books, one click cancels until booking closes;
- one meeting per child and teacher, and an adult never holds two slots at
  the same time, so their meetings follow one another;
- two parents clicking the same slot at once: the database gives it to one
  of them and tells the other to choose again;
- a confirmation email with the time, the teacher and the place; every time
  is shown in the school's time zone;
- teachers see their own schedule with the children's names.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_meeting_security.xml",
        "data/mail_template.xml",
        "views/meeting_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
