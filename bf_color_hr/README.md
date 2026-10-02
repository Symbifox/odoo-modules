# Symbifox Color for Employees (`bf_color_hr`)

Bridge between [`bf_color`](../bf_color) and Odoo Employees. Installs by itself
when both are present.

## What it does

Each employee takes a free color, set once on the employee form. Every agenda
that sorts by employee (shifts, for instance) shows it, so Dr A is red
everywhere; each person can still keep their own color for Dr A.

Employee tags take a free color too, shown on the employee's tags in the form,
the list and the kanban.

The color is also part of the public employee profile, which Odoo uses for
people without HR rights: without it, reading an employee would fail for them.

## Requirements

Odoo 18 (`hr`), `bf_color`.

## Licence

LGPL-3. See `LICENSE`.
