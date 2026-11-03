# Irregularities — Daily digest section (`bf_irregularity_digest`)

A bridge between `bf_irregularity` and `daily_todo_digest`: the reader's open
irregularities appear in the daily digest email, beside their activities, tasks
and meetings. It installs itself as soon as both modules are present.

## What it does

- Adds an **Irregularities to look at** section to the daily digest, one line
  per finding: the detector that raised it, the record it points at as a link,
  and what is wrong.
- Colours each line by severity — critical red, warning amber, info grey — and
  lists the most serious first.
- Shows **only the findings owned by the reader**. The section is built per
  recipient and never widens what the registry's own record rule allows.
- Caps the table at **15 rows** and adds a “See all N” link to the registry when
  there are more, so a long list does not swallow the digest.
- Prints **nothing at all** when the reader has no open finding: no empty table,
  no heading.

## Settings

On `daily.digest.config`: `include_irregularities` (on by default) removes the
section for everyone.

## One implementation note

The section is inserted before the digest's `<!-- Divider -->` marker, carrying
its own table row. The marker sits between two `<tr>` of the envelope table, and
an HTML5 parser moves stray content out of a table — so a section inserted bare
would be rendered above the card instead of inside it.

## Dependencies

`bf_irregularity`, `daily_todo_digest`. The French translation is in
`i18n/fr_CA.po`.
