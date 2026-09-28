{
    "name": "Symbifox École : présences",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Roll call by half-day or period, absences declared and justified by the "
               "families on the portal, same-day notice of an unjustified absence",
    "description": """
Attendance
==========

- attendance mode per group: morning and afternoon (elementary) or each
  period (secondary); no regulation sets the frequency;
- a teacher takes the roll call of the groups they teach: everyone present by
  default, the absences the family already declared are pre-filled and
  justified;
- when the roll call is closed, every unjustified absence tells the adults who
  receive notices, the same day, at most once per student and day;
- on the family portal: declare an absence to come (days, morning or
  afternoon, reason) or justify one after the fact; a late declaration
  justifies the roll calls it covers;
- the office manages the reasons (justified or not, offered to families or
  not), records declarations taken by phone, and lists the students with
  repeated unjustified absences (an aid: LIP s. 18 applies to public schools,
  the duty to report under the Youth Protection Act to every school).
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_attendance_security.xml",
        "data/attendance_data.xml",
        "views/attendance_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
