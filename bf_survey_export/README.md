# BF Survey Export

A survey's questions and possible answers in an Excel workbook, from one button
on the survey form. No respondent's answers, ever.

## Why

A questionnaire gets reviewed, rewritten and approved outside Odoo: by the
person who commissioned it, by a partner who co-signs it, by a translator. Odoo
can only show it one page at a time, as a respondent would see it, or print it
as a form to fill in. Neither can be sorted, annotated line by line, or sent
back with "change 3.5 and drop the last two choices of 4.2".

The first such workbook was made by hand, for a questionnaire with sections,
matrices, percentage allocations, a conditional question and "please specify"
boxes. This module produces the same workbook from one button.

## What it does

**Export questions (Excel)**, in the header of the survey form, downloads
`<survey title> - questions.xlsx`. The button shows as soon as the survey has a
line, section or question.

**Questions** sheet, one line per question under a line per section:

| column | content |
|---|---|
| Section | the section the question belongs to (empty for a question placed before the first section) |
| No. | the question's number, only when [BF Survey Numbering](../bf_survey_numbering) numbers the survey |
| Question | the title |
| Answer type | Odoo's own label for the type (and the matrix's subtype) |
| Mandatory | Yes / No |
| Help text shown | the description shown to the respondent, as plain text (lists keep their numbers or bullets, table cells are separated by `|`), the placeholder of a text, number or date field, and the labels under a scale |
| Possible answers (or matrix columns) | one `☐` line per choice (an image-only choice reads "(image)"); for a scale, one line per value |
| Rows (matrix or allocation) | one `☐` line per row of a matrix, or of a percentage allocation when a module adds that type |
| "Please specify" box | the box's label, when the question has one |
| Shown when | the answers that show a conditional question, joined by "or" |

What the respondent sees decides what the workbook shows:

- Odoo keeps the choices, rows and comment box of a question whose type was
  changed afterwards, and hides them by type. The workbook hides them too: a
  question turned into a text box lists no choices.
- A comment box counted as an answer is shown by Odoo as one more choice: it
  is listed with the choices.
- Conditions follow the survey's layout, as Odoo applies them. With one
  question per page, a trigger placed after its question is void. On one page,
  the question shows as soon as its trigger is ticked, in any order. With one
  page per section, a trigger later in the same section works, one in a later
  section comes too late. A trigger that is no longer a choice question is void
  everywhere. Void triggers are not listed, and a question left with none says
  it is never shown.
- A survey that draws its questions at random ignores conditions: the column
  stays empty.
- A section line carries the section's help text, and says how many questions
  are drawn from it when the survey draws them at random and the section holds
  more than that.

**Possible answers** sheet: one line per choice, matrix column or row, with its
section and question, to filter, sort or rewrite.

The workbook is written in the language of the person who exports it, with the
survey's own translations.

## What it never puts in the workbook

- **Respondents' answers.** Nothing reads `survey.user_input`; the tests file a
  response and look for it in every part of the `.xlsx` archive.
- **The answer key.** Which answer is correct, how many points it is worth and
  the expected number or date are never read: the tests check that the
  workbook is the same, cell for cell, with or without a key, so the workbook
  of a certification can be given to someone who will take it.
- **Formulas or links.** Every cell is written as text: a title typed as
  `=HYPERLINK(...)` stays a title.

A text longer than a spreadsheet cell holds (32,767 characters) is cut and
ends with "[truncated]", rather than left out.

## Access

The download is read as the person who asks, never as superuser, under the
same rules as the survey form: a survey restricted to some users, or a person
without access to Surveys, gets the same "not found" as a survey that does not
exist.

## Configuration

None. Install the module.

## Dependencies

`survey`, and the `xlsxwriter` Python library that Odoo already requires.
