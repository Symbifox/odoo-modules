{
    "name": "Symbifox École : carnet de notes",
    "version": "18.0.1.0.1",
    "category": "Education/School",
    "summary": "Evaluations and grades by course, results by competency and discipline, "
               "the 20/20/60 weighting over the three terms and the ministerial exams",
    "description": """
Gradebook
=========

- subjects of the Québec curriculum, each with its way of giving results: by competency
  (language of instruction, second language, mathematics), by theory and practice
  component (science and technology) or a single discipline result;
- the three terms of the school year with their report card deadlines and their
  20/20/60 weighting, as data: a government order that moves a date is entered with its
  reference, not coded;
- a course is a subject taught to a group; its teachers give the grades of their own
  courses and nobody else's;
- evaluations graded out of any maximum, weighted among those that judge the same
  competency; the result of each competency, of each discipline and the group's average
  are computed from the grades, never typed twice;
- final results (20/20/60 over the terms), the ministerial exam worth 20 % of the final
  result (50 % in the fourth and fifth years of secondary school), the 60 % success threshold
  and the units earned at the secondary level.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_core"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_school_gradebook_security.xml",
        "data/subject_data.xml",
        "views/gradebook_views.xml",
    ],
    "installable": True,
    "application": False,
}
