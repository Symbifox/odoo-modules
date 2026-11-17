# BF Survey Print

Printing a survey breaks at the right places: a section starts a new sheet, and
a question is never cut in two.

## Why

Odoo's printable survey page — the *Print* button of a survey, and the page a
respondent reaches to review the answers — carries **no page-break rule at
all**. The break rules of the `survey` module live in
`survey_templates_results.scss`, and that file is about the *statistics* page:
the printable questionnaire itself is left to break wherever the text happens
to fall.

Measured on a real questionnaire of 41 questions in 8 sections, printed from
Chrome with the default margins:

| | Odoo 18 alone | with this module |
|---|---|---|
| Questions cut between two sheets | **12** of 41 | **0** |
| Sections beginning at the top of a sheet | 2 of 8 | 7 of 8 — the first one follows the survey title on sheet 1 |
| Sheets | 19 | 23 |

Among the twelve was the consent matrix of the last section, split between two
sheets. A questionnaire printed to be filled in on paper and handed back cannot
afford that: a cut question is a question answered twice, or not at all.

A second questionnaire, of 19 questions in 4 undescribed sections, printed
**none of its section titles** at all — see the last point below.

## What it does

Print rules only, in `@media print`, plus one condition fixed in the core
template. No field, no setting, nothing new on screen.

- **A section starts a new sheet.** The sections of a survey laid out one page
  per section are the places its author already chose.
- **A question is never split between two sheets.** Title, help text, choices
  and matrix travel together. A question *taller* than a sheet is of course
  still broken — the constraint cannot be met — so what is left of it is kept
  readable: a matrix repeats its header row on the next sheet, and no row of it
  is ever cut.
- **A section title is never left alone** at the foot of a sheet.
- **The text column uses the width of the sheet.** On screen the questionnaire
  is a narrow column, which reads well; on paper that column left 40 mm of
  margin on either side, which split questions that would otherwise have
  fitted. The page keeps a 14 mm margin, so nothing lands in the strip a
  printer cannot reach.
- **A section with no description is printed too.** On a blank page there is no
  answer, so `questions_to_display` is empty and the core shows a section only
  if it is described. An undescribed section printed neither its title nor the
  separator the breaks ride on. Here a blank page shows a section as soon as it
  holds a question — which is already how the core decides to show the
  questions themselves. A filled page is untouched.

## How it is held together

The stylesheet rides on two hooks of `survey.survey_page_print`: the `<hr>` the
template renders before every section but the first, which marks exactly the
sheet boundaries to force, and the `.js_question-wrapper` box around each
question. Neither is an API. An Odoo upgrade that renamed or moved one of them
would stop the breaks **without a single error in the log** — the printout
would simply start cutting questions again.

So the tests do not test the CSS; they read the page a browser is served and
check the hooks are still there: one separator per section but the first, as a
direct child of the fieldset; one wrapper per question; one header per section,
described or not; a matrix header row inside a `thead`. One of them goes
further and fetches the stylesheet the page links to, so that losing the sheet
itself — a typo in the asset path, a renamed bundle — cannot leave the rest
green. They run on a blank page, on a filled one, and as a reader who is not
logged in.

## Compatibility

Odoo 18. Depends on `survey` alone, and nothing in it is specific to a brand or
a theme. A custom question type is kept whole like any other **provided it
renders on the printable page at all**: the suite's percentage allocation does,
and was measured whole; its file upload question does not render there yet, so
printing one shows its title and nothing under it — which this module cannot
fix from a stylesheet.
