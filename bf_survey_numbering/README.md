# BF Survey Numbering

A survey numbers its own sections and questions (2, 2.1, 2.1.1), and a question
can be moved from one section to another without retyping anything.

## Why

A questionnaire that people discuss needs numbers. An advisor reading the
results wants to say "look at 2.4, 3.5 and 5.6 together"; two advisors
comparing notes want to point at the same line. Odoo has no numbering, so the
numbers end up typed into the titles, and the first question that moves makes
every number after it wrong.

Moving the question is the second half of the problem. In Odoo 18 the list of
questions in the survey form is paged at **40 lines**: on a questionnaire of 52,
a question can only be dragged among the lines on screen, never into a section
further down. Seen on a real questionnaire during a working session, where
questions had to be recreated in their new section rather than moved.

## What it does

### Numbering

Tick **Number sections and questions** (survey form, *Options* tab).

| line | number shown |
|---|---|
| a section | `2.` before its title |
| a question of that section | `2.1`, `2.2`… |
| a conditional question shown by an earlier question **of the same section** | `2.1.1`, `2.1.2`… under that question, without taking a number of the section |
| a question before any section | `1`, `2`… |

- Numbers are **computed, never written**. Move a line and the others renumber;
  untick the box and every title is back to what it was. The title of a
  question is not touched, in any language.
- Shown in the survey editor (a `#` column), the form the respondent fills in
  and its breadcrumb, the printable page, the results page, and through
  `display_name` wherever Odoo names a question (answer lines, pickers). Not in
  the conditional-display picker of the question form: it names the *answer*,
  whose label the core builds from the bare question title.
- `bf_survey_answer_sheet`, when installed, prints the same numbers on the
  answer sheet it files.
- A conditional question numbered under its trigger needs the trigger to come
  first *in the same section*. A question shown by a question of another section
  is numbered as an ordinary question of its own section.

### Numbers already typed by hand

A survey numbered by hand would show `2.1 2.1 Founding year`. With numbering on,
the form says how many titles start with a typed number, and **Remove the typed
numbers** strips those that **repeat the line's own number** — `2.1 ` on
question 2.1, `2. ` on section 2 — in the en_US source and every installed
language, each on its own.

The removal cannot be undone, so it never guesses. A title that only *starts
like* a number (`2.5 hours a week` on question 3.4, `2026 objectives`) and a
typed number that no longer matches the line's place are left as typed, and
named in a notice to be fixed by hand. A title that would be left empty is left
alone too.

⚠️ The number is not in the title any more once removed: anything that reads
`survey.question.title` directly (an export, a script, an assistant reading the
survey over RPC) sees the bare title. Read `bf_number` alongside it.

### Moving questions

- **Drag and drop across the whole survey.** The editor shows up to 1,000 lines
  on one screen, so any line can be dragged anywhere.
- **Move questions** (survey header), the ✥ button on a line, or *Action ▸ Move
  to another section* on the list of questions (several at once): pick a
  destination section and place the lines **at its end**, **at its start**, or
  **after one of its questions**.
- A **section moves with all its questions**. It is placed just before the
  destination section (*start*) or just after the destination's last question
  (*end*); never after a question, which would cut a section in two. A section
  and questions picked from elsewhere are not moved together: the stray
  questions would land in the wrong section.
- **Responses in progress** are counted in the form: the new order applies to
  them at once, and a question moved into a section they have already passed
  will not be shown to them.
- **Dropping a dragged line saves the survey**, as the survey editor already
  does after every question edited in its dialog. Until saved, the web client
  only refreshes the lines whose own values changed, and the `#` column showed
  old and new numbers mixed.
- **A move that would hide a question is refused.** If a conditional question
  would come before the question that shows it — or a trigger after the
  questions it shows — the respondent would never see it, and the move says
  which lines are concerned instead of applying.
- After a move every line has its own sequence (10, 20, 30…), so most lines of
  the survey are rewritten (and their `write_date` stamped): a move is an
  explicit reorder. Real
  questionnaires often have several lines on the same sequence, and a tie is
  settled by id, which would put a moved line back where it came from.

## How it is held together

- `survey.question.bf_number` is a **non-stored** computed field. A stored
  number would be rewritten on every question of a survey whenever one moves,
  stamping `write_date` on lines nobody touched, and installing the module would
  stamp every question of every survey.
- The order is spelled out (`sequence`, then id): in the editor the lines are
  new records, and `sorted()` alone keeps those in the order they came in,
  ignoring the sequence just dragged.
- The number is added to the pages by template inheritance. Where a heading
  carries its title as a `t-field` on the heading itself, the `t-field` moves to
  an inner span (still editable in place) and the number goes before it.
- The move form (`bf.survey.question.move`) is a transient model closed to
  everyone but its creator by a global record rule.

## Limits

- Numbering is a display: searching or grouping by number is not possible.
- Random question selection on a section is not taken into account: the numbers
  follow the survey's order, not the draw.
- Live sessions: the participant's screen uses the fill form and is numbered,
  the host's screen is not.
- A conditional question numbered under its trigger is still placed where its
  sequence puts it: if other questions sit between them, it reads 2.1, 2.2,
  2.1.1.

## Tests

`tests/test_numbering.py` (the rules, the editor's new records, typed numbers in
two languages), `tests/test_move.py` (every placement, sections, triggers,
sequence ties, access), `tests/test_rendering.py` (the fill form in both
layouts, the printable page and the results page, read as served).

## Requirements

Odoo 18 and its `survey` module. `bf_survey_answer_sheet`, when installed, prints
the same numbers on the answer sheet it files; neither module depends on the
other.

## Languages

The interface is written in English and ships a French (`fr_CA`) catalogue.

## Licence

LGPL-3. See `LICENSE`.
