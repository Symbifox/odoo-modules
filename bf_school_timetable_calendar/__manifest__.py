{
    "name": "Symbifox École : horaire et calendrier scolaire",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "The timetable follows the school calendar: the office enters each day once",
    "description": """
Timetable and school calendar
=============================

- when a school keeps its calendar for a year, the days of its timetables follow
  it: a pedagogical day, a break or a holiday entered in the school calendar
  leaves the cycle of the timetable, and the days of the cycle are numbered
  again;
- an exam day is still marked in the timetable, on a class day;
- when the calendar receives the first days of a school year (at install, or
  later), it first takes the days its timetables already hold: nothing entered in
  a timetable is lost;
- a school with no calendar for a year keeps its timetable as before.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_timetable", "bf_school_calendar"],
    "data": [
        "views/timetable_views.xml",
    ],
    "post_init_hook": "_seed_calendar_from_timetables",
    "installable": True,
    "application": False,
    "auto_install": True,
}
