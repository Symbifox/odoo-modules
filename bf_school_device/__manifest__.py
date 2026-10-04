{
    "name": "Symbifox École : postes des élèves",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Student accounts in the school's directory and Blue Fox OS computers "
               "lent to students, bridged to the Blue Fox OS policy",
    "description": """
Students' computers
===================

Part of Symbifox École: the school's old computers, refurbished
with Blue Fox OS, in the students' hands, in the lab and at home.

- one account per student in the school's directory (Authentik): a user name
  drawn from a sequence (never the permanent code, which spells the name and
  birth date), groups taken from the student's level and class, active while
  the student is enrolled this year and suspended otherwise;
- a job carries every change to the directory every 15 minutes, by comparing
  what the directory must hold with what it was last sent, so a change of
  enrolment, class or name reaches it without anyone thinking of it;
- a password a young student can type (two words and four digits), drawn on
  request, set in the directory and shown once to the person who asked;
- loans of the computers enrolled under a loan profile (bf_policy shared
  seats): recording the loan writes the student as the machine's borrower, so
  the machine opens to them at its next policy sync; returning it, or marking
  it lost, closes it again; one open loan per computer, enforced in the
  database; overdue loans and loans of students who left are filtered out;
- the adults who receive the school's notices get an email when the computer
  is lent (computer, due date, condition noted), seven days before the due date,
  and once if it is overdue, each in their own language and the school's layout;
- on the family portal, each child's card shows the computer lent and its
  due date.

The lab computers need nothing here: a lab profile lets the student groups log
in (students, students-sec1, students-301).
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "bf_policy"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_device_security.xml",
        "data/bf_school_device_data.xml",
        "data/mail_templates.xml",
        "views/device_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
