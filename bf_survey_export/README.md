# BF Survey Export

A survey's questions and possible answers in an Excel workbook or a PDF, and
one person's answers in a PDF, each from one button. The PDFs take the colours,
logo and font of a theme chosen on the survey.

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

## PDF: the blank questionnaire

**Export questions (PDF)**, next to the Excel button, prints the questionnaire
to hand out or to have approved: a coloured header band with the logo, the
survey's description, then each section and its questions with their number,
help text, type, "Mandatory" mark and condition, and what the respondent fills
in (round or square boxes for the choices, the grid of a matrix, a line or a
box for a typed answer, the "please specify" box). The survey's end message
closes it. It reads the same lines, with the same verdicts on conditions and
leftover choices, as the workbook: no respondent's answers, no answer key.

A section title is kept on the page of its first question.

## PDF: one response

**Export answers (PDF)**, in the header of a response (Surveys >
Participations), prints what one person answered, usually on two or three
pages: who answered, their organisation and when they submitted, then one line
per question.

- Only the questions this person was shown: a question hidden by its
  condition is left out, as Odoo leaves it out of its own print, and a section
  whose questions were all hidden is not printed.
- Only the choices they ticked, and their "please specify" text under the name
  of its own box.
- A matrix gives the column chosen on each row; a percentage allocation, a bar
  per row; a section left empty takes one line.
- No score and no answer key.

## PDF themes

**Surveys > Configuration > PDF themes** (survey administrators): a theme is a
kicker line above the title (a programme's name, say), a footer signature,
three colours (main, accent, text), a logo for the coloured band (a light or
white version reads best) and a font, either installed on the server or given
as a TrueType, OpenType or WOFF file that is embedded in each PDF.

Pick one on a survey, under **Options > PDF exports**. A survey without a
theme takes the report colours, logo (on a white tile) and name of the company
the person prints from (a survey has no company of its own). A
theme can be offered to one company or to all; survey officers can read
themes, only administrators change them.

Colours and font names end up in a stylesheet, where HTML escaping does not
protect anything: a colour is accepted only as a `#RRGGBB` code and a font
name only as letters, digits, spaces, commas, hyphens and quotes, and a value
that got into the database another way is replaced by its default when the
PDF is drawn.

## Access

Every export is read as the person who asks, never as superuser, under the
same rules as the survey form. For the workbook, a survey restricted to some
users, or a person without access to Surveys, gets the same "not found" as a
survey that does not exist. A PDF the person may not open is refused with
Odoo's access error before anything is drawn.

## Configuration

None for the workbook. For the PDFs, optionally a theme (above).

## Dependencies

`survey`, and the `xlsxwriter` Python library that Odoo already requires. The
PDFs use Odoo's own report engine (wkhtmltopdf): nothing more to install.
