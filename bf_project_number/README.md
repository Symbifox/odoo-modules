# Project Number (`bf_project_number`)

A three-digit project number, assigned on its own, and editable by hand.

## Why

Odoo already numbers projects — by their technical `id`, which starts wherever
the database happens to be (21, 31, 32…) and cannot be corrected. Hand-kept
project books, on the other hand, count from 001 and serve as the billing
reference. This module provides that numbering.

## What it does

- **At creation**: the project receives the **smallest number still free**.
  A number freed by a deleted or renumbered project is taken back, instead of
  leaving a hole.
- **By hand**: the number stays editable. A taken number is refused, naming
  the project that holds it.
- **At install**: projects that already existed are numbered in the order of
  their creation, so the column does not open empty.
- **Where it reads**: to the left of the name in the list, as a badge on the
  kanban card, on the form, and in the search bar.
- **Duplication**: copying a project (a "duplicate me" template) yields a
  **fresh** number.

The project name is not touched: nothing to rename, nothing to rewrite.

## What it does not do

- **It does not change the default order** of project lists and kanbans
  (`is_favorite desc, sequence, name, id`). The number is a sortable column:
  one click on its header sorts by number. Forcing that order on every
  database would be a display choice, not a numbering.
- **Uniqueness is database-wide**, not per company: a number designates one
  project, which is the point of a billing reference.
- **Beyond 999**, the number keeps its digits (1000) but sorting a text column
  would place it before 999. No effect under a thousand projects.

## ⚠️ Uninstalling loses the numbering

Uninstalling the module drops the column (`DROP COLUMN … CASCADE`): **all
numbers are lost**. And the takeover of existing projects only runs at the
FIRST install: reinstalling therefore renumbers everything from 001 in
creation order, silently. A number quoted on an invoice or in a project book
would then designate another project. Before uninstalling, export
`project.project` with its number column.

## Tests

`tests/test_project_number.py` — assignment, hole filling, batch creation,
archived project keeping its number taken, duplication, number form, disguised
duplicate ("7" versus "007"), refusal naming the holder.
