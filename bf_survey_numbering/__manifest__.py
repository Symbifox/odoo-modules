{
    "name": "BF Survey Numbering",
    # 18.0.1.0.0: un questionnaire dont on discute veut des numéros (« la 2.5,
    #   la 3.2 »), dans le formulaire comme dans le rapport, et les numéros tapés
    #   à la main dans les titres se décalent dès qu'on déplace une question.
    #   L'éditeur ne laissait pas non plus déplacer une question d'une section à
    #   l'autre : la liste imbriquée est paginée à 40 lignes, et un questionnaire
    #   réel en comptait 52.
    # 18.0.1.1.0: a question can be moved from its own dialog. The editor
    #   opens a question in a dialog that hides its section, so it could only
    #   be moved from the list; the dialog now shows the section and a Move
    #   button, and the editor reloads after the move so that the list behind
    #   it never keeps the old order.
    "version": "18.0.1.1.0",
    "category": "Marketing/Surveys",
    "summary": "Number a survey's sections and questions by themselves (2, 2.1, "
               "2.1.1), and move questions from one section to another",
    "description": """
Survey numbering
================

A questionnaire people talk about needs numbers: "question 2.5", "the whole of
section 3". Typing them into the titles works until the first question moves,
then every number after it is wrong and has to be retyped by hand.

- **Numbering by the survey itself.** Tick *Number sections and questions* on a
  survey and every section gets its rank (1, 2, 3...), every question its rank
  within the section (2.1, 2.2...), and a conditional question shown by an
  earlier question of the same section a sub-number (2.12.1). Numbers follow
  the order of the questions: move one and the others renumber. The titles are
  never touched; the number is shown before them.
- **Shown where the questions are read**: the survey editor, the form the
  respondent fills in and its breadcrumb, the printable page, the results
  page, and the name of a question wherever Odoo shows one (answer lines,
  pickers).
- **Typed numbers removed in one click.** A survey numbered by hand would show
  "2.1 2.1 Founding year": the form says how many titles start with a typed
  number and removes those that repeat the line's own number, in every
  language. Any other typed number is named and left as typed.
- **Moving questions across sections.** The editor lists every question on one
  screen, so a question can be dragged anywhere, not only within the first 40
  lines. A *Move* button on each line also sends one or more questions — or a
  whole section with its questions — to the start or the end of another
  section, or after a given question. The same button is in the dialog of a
  question, next to the section it belongs to. A move that would place a conditional
  question before the question that shows it is refused, and the form warns
  when responses are in progress.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey"],
    "data": [
        "security/ir.model.access.csv",
        "security/survey_question_move_rules.xml",
        "wizard/survey_question_move_views.xml",
        "views/survey_survey_views.xml",
        "views/survey_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_survey_numbering/static/src/js/question_page_save_on_drop.js",
        ],
        "survey.survey_assets": [
            "bf_survey_numbering/static/src/scss/survey_numbering.scss",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
