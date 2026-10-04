{
    "name": "Symbifox École : contenus de cours",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Course contents in the eLearning app, given to the students of school groups "
               "and their parents, and renewed from one school year to the next",
    "description": """
Course contents
===============

- an eLearning course is tied to one or more school groups: their students, and
  the adults who receive the school's notices for them, are its attendees, kept
  up to date as students join or leave a group;
- a school course is shown to its attendees only, without comments, reviews nor
  karma: students do not appear to the other families;
- "Renew for another year" copies a course with its contents and files and ties
  the copy to the groups of the next year;
- on the family portal (Courses): the courses of the student or of each child.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "website_slides"],
    "data": [
        "security/bf_school_slides_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "wizard/renew_course_views.xml",
        "views/slide_channel_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
