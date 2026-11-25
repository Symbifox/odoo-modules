{
    "name": "BF Survey Export",
    # 18.0.1.0.0: a questionnaire's questions and possible answers, exported to
    #   an Excel workbook from the survey form, to be reviewed outside Odoo.
    # 18.0.1.1.0: the blank questionnaire and one response as PDF, in the
    #   colours of a theme chosen on the survey.
    "version": "18.0.1.1.0",
    "category": "Marketing/Surveys",
    "summary": "Export a survey's questions to Excel or PDF, and a response "
    "to PDF, in the colours of a theme chosen on the survey",
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

The same questionnaire, and its answers, as PDF:

- **Export questions (PDF)**, on the survey: the blank questionnaire, to hand
  out or to have approved. Sections, numbers, help texts, the choices to
  tick, the grids of a matrix, the conditions. No answers, no answer key.
- **Export answers (PDF)**, on a response: what one person answered, on a few
  pages. Only the questions they were shown, only the choices they ticked,
  their "please specify" text under the box it belongs to. No score, no
  answer key.
- **PDF themes** (Surveys > Configuration): a kicker line, a footer
  signature, three colours, a logo for the coloured band, a font (installed
  on the server, or a font file embedded in the PDF). Pick one on a survey,
  under Options. Without a theme, the PDFs take the colours and logo of
  the company the person prints from. A theme serves a programme run for a partner as well as the
  company itself.
- Read as the person who prints, never as a superuser: whoever cannot open a
  survey or a response in Odoo cannot print it either.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "security/ir.model.access.csv",
        "security/bf_survey_pdf_theme_security.xml",
        "report/survey_pdf_reports.xml",
        "views/survey_survey_views.xml",
        "views/bf_survey_pdf_theme_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
