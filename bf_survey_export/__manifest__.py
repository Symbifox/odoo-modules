{
    "name": "BF Survey Export",
    # 18.0.1.0.0: a questionnaire's questions and possible answers, exported to
    #   an Excel workbook from the survey form, to be reviewed outside Odoo.
    "version": "18.0.1.0.0",
    "category": "Marketing/Surveys",
    "summary": "Export a survey's questions and possible answers to an Excel "
    "workbook, without any respondent's answers",
    "description": """
Survey export
=============

A questionnaire gets reviewed, rewritten and approved outside Odoo: by the
person who commissioned it, by a partner who co-signs it, by a translator.
Odoo can only show it to them one page at a time, as a respondent would see
it, or print it as a form to fill in.

- **One button on the survey form**: *Export questions (Excel)*.
- **The Questions sheet**: one line per question, under its section, with its
  number (when the survey numbers its questions), its type, whether it is
  mandatory, the help text shown to the respondent, the possible answers, the
  rows of a matrix or of a percentage allocation, the "please specify" box and
  the condition under which the question is shown.
- **The Possible answers sheet**: one line per possible answer, to filter, sort
  or rewrite.
- **No respondent's answers, ever**, and no answer key: which answer is
  correct and how many points it gives are left out, so the workbook of a
  certification can be handed to someone who will take it.
- **In the language of the person who exports**, with the survey's own
  translations.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "views/survey_survey_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
