{
    "name": "Symbifox École : remise de travaux",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Students hand in their work on the portal, the teacher returns a corrected "
               "copy with a mark; scored quizzes through Surveys",
    "description": """
Handing in work
===============

- on a homework, the teacher chooses how it is handed in (not online, files, or
  a scored quiz), attaches documents for the students and sets the total it is
  marked out of;
- on the portal, the student (with their own account) or a parent who holds
  parental authority hands in files before the due date; late work is accepted
  and flagged, or refused, per homework;
- the teacher returns a corrected copy, a comment and the mark; the student and
  the parents see the mark once the work is returned;
- a quiz is a scored survey: its score, out of the homework's total, becomes the
  mark of the hand-in;
- emails, offered by the school and chosen by each adult (or student) on the
  portal: one when work is returned, and a daily summary (work returned, work to
  hand in soon or late, new course contents).
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_homework", "survey"],
    "data": [
        "security/bf_school_work_security.xml",
        "security/ir.model.access.csv",
        "data/mail_templates.xml",
        "data/ir_cron.xml",
        "views/school_views.xml",
        "views/submission_views.xml",
        "views/homework_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
