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
* When the expense report is approved (the *Approve* button, or the duplicate
  wizard), a **confirmed** usage line is created for each expense linked to a
  benefit, with origin "expense", carrying the amount actually incurred in the
  company's currency.
* If the approval is withdrawn (report refused, reset to draft, expense removed
  from the report), the line is withdrawn too, with a note on the expense's
  chatter. A new approval creates a new line, at the amount of the day.
* The link is bidirectional: the usage line cites the expense, the expense cites
  the line. One expense cannot produce two usages.

⚠️ In Odoo 18 an expense's state is computed from its expense report and never
goes through `hr.expense.write`. Version 18.0.1.0.0 listened to that write
only, so it never fired outside its own tests. The bridge now listens to the
report, and the 18.0.1.0.1 migration creates the usage lines that approved
expenses should have produced.

## What it does not do

It blocks nothing. An expense linked to a benefit the person was not entitled to
that day still goes through: the usage line then carries its "no open
entitlement" flag, like any manual entry. Flagging beats blocking, because it is
visible.

## Languages (18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The note on the usage line created by an approval is written in the language of the person who spent, who reads their own register (their user's language, else their work contact's, else the company's), not in the approver's. The note left on the expense when its usage line is withdrawn follows the user who acts.

## Tests

17 tests, played through the real approval path of the expense report.

```
odoo -d <database> -u bf_employee_experience_expense --test-enable \
     --test-tags /bf_employee_experience_expense --stop-after-init --http-port=8180
```

## Changelog

### 18.0.1.1.0

- Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`.
- The note on a usage line is written in the language of the person who spent, not the approver's. See the Languages section.

### 18.0.1.0.1

- Usage lines follow the expense report's approval, refusal and reset, instead
  of a write on the expense that Odoo 18 never makes.
- The amount is taken in the company's currency.
- Migration: approved expenses without a usage line get one.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). You may run it for your
own internal business operations; providing it to third parties as a hosted,
managed or resold service requires a written agreement. This version converts
to LGPL-3.0-or-later on 2030-10-21.
