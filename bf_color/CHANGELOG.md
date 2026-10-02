# Changelog — Symbifox Color (`bf_color`)

## 18.0.1.1.1 — 2026-10-02

- Security: "Assign missing colors" wrote in sudo for anyone who could read a
  rule; it now requires the right to change the rule, and its button is shown
  to administrators only. Values are searched within the rule's company.
- Security: the owner of a color override, of a swatch or of a swatch entry can
  no longer be changed after the fact (record rules are re-checked after the
  write). A user could turn their own override into a company color, or move a
  line into a shared swatch.
- Security: a free color is always stored as a clean `#RRGGBB`, or refused; the
  color field shows nothing else in a style attribute.
- A value followed by a rule shows only its own color and the overrides on it,
  never the result of its own rules (which ran in sudo).
- Archived rules no longer apply. Rule lines follow their rule's company.
- "Assign missing colors" counts the colors in use in SQL.

## 18.0.1.1.0 — 2026-10-02

- Rules follow the value's own color: a doctor's color set on the employee
  colors every agenda sorted by employee. A line of the rule still wins.
- Many2many criteria (tasks by tags), with the order of the rule's lines as the
  priority, and "Listed values only".
- A rule can only target a model wired to the mixin; before, a rule on any
  other model saved and colored nothing.
- Values without a color get the least used color of the fallback swatch,
  written on the value, when they are created or with "Assign missing colors".
- Calendar views and kanban cards paint free colors, softened like Odoo's
  palette, with readable text, in the popover and the filter legend.
- Writing a free color never writes through a related color index.

## 18.0.1.0.1 — 2026-09-26

- Security: the entries of a shared company swatch could be changed or deleted
  by any internal user. They now follow their swatch: read by everyone who can
  read the swatch, changed by its owner, or by an administrator for a shared
  swatch.

## 18.0.1.0.0 — 2026-09-26

- First release: free hex colors resolved per user, per company, by automatic
  rules and per record; closest Odoo palette index and readable text color;
  personal and shared swatches; color picker; contact tags wired.
