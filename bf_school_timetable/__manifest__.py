{
    "name": "Symbifox École : horaire",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "The timetable in a cycle of school days, rooms with no double booking, the family's "
               "week in the portal and a calendar link for each child",
    "description": """
Timetable
=========

- the periods of the day and a cycle of school days (9 or 10, or a plain week): the timetable says
  "day 3, period 2" and the calendar says which date is which day of the cycle, skipping
  holidays and pedagogical days without losing its place;
- the classes of each course, with their room; nobody, no room and no group is in two places at
  once, unless the group splits;
- the classes load from a CSV file as a timetable program exports them;
- the family's portal shows each child's next seven days, and a private link lets their calendar
  program subscribe to it (the link can be replaced at any time);
- a printable timetable of each group.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_gradebook", "bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_timetable_security.xml",
        "report/group_timetable.xml",
        "views/timetable_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
