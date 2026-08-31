# Employee Experience — Expenses bridge (`bf_employee_experience_expense`)

An approved expense becomes a confirmed usage line, at the real amount.
Auto-installs when `bf_employee_experience` and `hr_expense` are both present.

Without this bridge, the cost of a benefit comes from the model: a reference
amount multiplied by a headcount. That is an estimate. With the bridge, an
approved expense carries the figure from the field, and the usage line takes it
as is.

## What it does

* A "Benefit" field on the expense and on the expense product. The product acts as
  the default: link the "Training" product to the benefit once, and every
  subsequent expense follows.
* On the move to approved state, a **confirmed** usage line is created, with
  origin "expense", carrying the amount actually incurred.
* The link is bidirectional: the usage line cites the expense, the expense cites
  the line. One expense cannot produce two usages.

## What it does not do

It blocks nothing. An expense linked to a benefit the person was not entitled to
that day still goes through: the usage line then carries its "no open
entitlement" flag, like any manual entry. Flagging beats blocking, because it is
visible.

## Tests

7 tests.

```
odoo -d <database> -u bf_employee_experience_expense --test-enable \
     --test-tags /bf_employee_experience_expense --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
