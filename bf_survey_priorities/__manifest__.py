{
    "name": "BF Survey Priorities",
    # 18.0.1.0.0: a "priority 1, 2, 3" matrix read as a weighted trend of all
    #   the respondents, and "one row per column" held at entry.
    "version": "18.0.1.0.0",
    "category": "Marketing/Surveys",
    "summary": "Weight the columns of a matrix (priority 1, 2, 3) and read the "
    "weighted trend of all respondents, on the results page, in Excel and PDF",
    "description": """
Survey priorities
=================

A matrix question asks each respondent to rank a few items: rows for the
needs, columns for *Priority 1*, *Priority 2*, *Priority 3*. Odoo counts the
ticks of each cell. It does not say which needs come out on top once all
the answers are put together.

- **Points on each column** of a matrix (for example 10, 5 and 2), set on
  the question and changed at any time: the trend is recomputed on the fly.
- **The weighted trend**: each row's points, its share of the total and how
  many respondents ticked it in each column, from the most to the least
  weighted. On the results page under the matrix (and under Odoo's own
  filters), in an Excel workbook and in a PDF in the colours of the survey's
  PDF theme.
- **The same weight for everyone**: when a respondent ticks several rows in
  the same column, the column's points are shared between those rows. One
  respondent can never weigh more than the column points added up.
- **Groups of rows** (optional): give rows a group (a skill, a theme, a
  department) and the trend is also added up by group.
- **One row per column**, an option of the matrix: the respondent cannot put
  two rows under the same column. Checked in the browser before the page is
  sent, and again by the server.
- The trend reads the responses as the person who looks at it: test entries
  are left out, and so is anyone they may not see.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["survey", "bf_survey_export"],
    "external_dependencies": {"python": ["xlsxwriter"]},
    "data": [
        "views/survey_question_views.xml",
        "views/survey_survey_views.xml",
        "views/survey_templates.xml",
        "report/survey_trend_report.xml",
    ],
    "assets": {
        # The survey pages' own bundle, where survey_form lives: in
        # web.assets_frontend the import is missing on every other public page.
        "survey.survey_assets": [
            "bf_survey_priorities/static/src/js/survey_form_priorities.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
