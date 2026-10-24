{
    "name": "Symbifox École : service de garde",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "School child care: periods and places, regular registrations, occasional bookings, "
               "daily sheets with arrivals and departures, who may pick up a child, leaving alone",
    "description": """
School child care
=================

Attendance only: what child care costs is billed by another module.

- periods per school: before school, lunch time, after school, days without
  class; hours, weekdays, levels (preschool and elementary only, s. 62.1 of the
  Act respecting private education), places, grace minutes;
- regular registration: periods and weekdays between two dates, asked by the
  family on the portal and put in force by the child care manager once the
  places are checked on every date;
- occasional booking on the portal: the school's notice, a full period, a
  closed day, a period that does not run or is not for the child's level are
  refused; the office may still book late; a late cancellation stays due;
- days without class: an activity with places, a minimum and a deadline; when
  cancelled, its bookings are cancelled at no charge and the families told;
- absence notices on the portal (a whole-day absence declared to the school
  counts too): the place stays due;
- daily sheet for the educator, phone-sized: the children expected, arrival and
  departure by buttons, who takes the child chosen among those allowed that day,
  lateness counted on the school's clock;
- who may pick up a child: the family links that allow it, people added by the
  adults with parental authority (dates, ID checked the first time), pick-up
  restrictions entered by the manager (the educator sees the name, never the
  detail), permission to leave alone signed on the portal or on paper;
- every change of who may pick up a child is emailed to every adult with
  parental authority;
- roles: the educator reads neither the family links nor the conduct.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_calendar", "bf_school_portal", "bf_school_attendance"],
    "data": [
        "security/bf_school_childcare_security.xml",
        "security/ir.model.access.csv",
        "data/mail_template.xml",
        "views/childcare_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": True,
}
