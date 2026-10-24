{
    "name": "Symbifox École : calendrier scolaire",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "The school calendar of each school and year: class days, pedagogical days, breaks, "
               "holidays and closures, and whether child care opens",
    "description": """
School calendar
===============

- one day of the calendar per school and date: class day, pedagogical day,
  school break, holiday (closed) or emergency closure, with an early dismissal
  time when there is one;
- whether child care opens that day: yes by default on class and pedagogical
  days, no on breaks, holidays and closures; the office may open it on a break;
- one button on the school year makes its class days, Monday to Friday, without
  touching the days already set;
- read by every staff member, kept by the Administration; the other school
  modules ask it what kind of day a date is, in the school's time zone.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_core"],
    "data": [
        "security/bf_school_calendar_security.xml",
        "security/ir.model.access.csv",
        "views/calendar_views.xml",
    ],
    "installable": True,
    "application": False,
}
