# BF Survey Priorities

A matrix that asks respondents to rank a few items (rows for the needs, columns
for *Priority 1*, *Priority 2*, *Priority 3*) read as a weighted trend of all the
respondents: which needs come out on top once every answer is put together.

## Why

Odoo's results page counts the ticks of each cell of a matrix. With ten needs
and three priorities, that is thirty counts to compare by eye, and a need ticked
once as priority 1 looks the same as one ticked once as priority 3. Giving each
column points (10, 5 and 2, for example) and adding them up row by row turns the
grid into one ranked list.

## What it does

**Points on the columns.** On a matrix question, the *Answers* tab shows a
*Points* column next to each column label. As soon as one column has points, the
question has a weighted trend. Points can change at any time: the trend is
computed when it is read, never stored.

**Groups of rows (optional).** A *Group* next to each row (a skill, a theme, a
department). When at least one row has a group, the trend is also added up by
group; rows without one are shown as "No group".

**The trend.** For each row: its rank, its points, its share of all the points
given, and how many respondents ticked it under each column. Rows without points
(nobody ticked them, or only under a column worth 0) are listed last, without a
rank. Shown:

- on the **results page**, under the matrix, following Odoo's own filters
  (completed only, an answer chosen elsewhere…);
- in **Weighted trend (Excel)** and **Weighted trend (PDF)**, two buttons in the
  header of a survey that has a weighted matrix. Both read the completed
  responses only. The PDF takes the colours, logo and font of the survey's PDF
  theme ([BF Survey Export](../bf_survey_export)).

**The same weight for everyone.** A respondent who ticks several rows in the same
column does not get to count more: the column's points are shared equally between
those rows (10 points over 4 rows = 2.5 each). One respondent can never give more
than the column points added up (17 with 10, 5 and 2). A column left empty gives
nothing. The trend says how many respondents ticked several rows in one column.

**What the results page counts.** Like the rest of Odoo's results page, the trend
follows the page's filters: by default it also counts the responses still in
progress, which the exports never do. The page says so; filter on completed
surveys to compare with an export.

**One row per column.** An option of the matrix, under *Options*: the respondent
may tick at most one row under each column. The page is not sent until the extra
rows are unchecked; the rows concerned are highlighted. The server applies the
same rule. Checking in the browser first matters: Odoo saves each valid question
of a page before it answers with the errors of the others, and when the survey
does not let respondents go back, a page sent twice would then fail for good.
A mandatory matrix with more rows than columns cannot use this option (nobody
could fill in every row): the question refuses to be saved that way, and so does
a row added, or a column removed, on its own. Turn the option on before the
survey is sent: a respondent who already has the page open does not get the
browser check, and when the survey does not let respondents go back, the
server's refusal can leave that page stuck. In a matrix
with several answers per row, one row can still be ticked under several
columns; for a ranking, use one answer per row.

## What it does not do

- It scores nobody: the trend is the reading of all the responses together. A
  respondent never sees points, and the survey's own scoring is not touched.
- Test entries are never counted.
- A row or a column deleted after responses came in drops those ticks, and the
  points of that column are shared again between the rows that remain.
- Everything is read as the person who looks at it: someone who cannot open the
  survey cannot export its trend (the route answers 404, as for a survey that
  does not exist), and the record rules on responses apply.

## Technical notes

- `survey.question.answer.bf_rank_points` (column), `bf_rank_group` (row);
  `survey.question.bf_one_per_column`; `bf_rank_weighted` and
  `survey.survey.bf_rank_has_weighted` are computed, not stored.
- `survey.question._bf_rank_trend(lines)` does the reading; the results page
  calls it from `_prepare_statistics`, with the lines Odoo already filtered.
- `survey.question._validate_matrix` adds the column rule after the native
  checks. The browser check lives in `survey.survey_assets`.
- Route: `/bf_survey_priorities/trend/<survey_id>` (`auth="user"`, never sudo).

## Licence

LGPL-3. © Les services de consultation Blue Fox, Inc.
