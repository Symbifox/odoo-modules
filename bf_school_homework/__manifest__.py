{
    "name": "Symbifox École : devoirs et agenda",
    "version": "18.0.1.0.2",
    "category": "Education/School",
    "summary": "Homework, studies, projects and announced tests per group, on the family "
               "portal and in the parents' own calendar (iCal)",
    "description": """
Homework and calendar
=====================

- a teacher posts homework, studies, projects and announced tests for the
  groups they teach, with the date given and the due date; list and calendar;
- on the family portal (Homework): what is due for each child, sorted by date;
- a personal calendar address (iCal) per adult: subscribed in Google Calendar
  or Apple Calendar, the due dates appear there and update by themselves; the
  adult can get a new address, which stops the old one.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_homework_security.xml",
        "views/homework_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
