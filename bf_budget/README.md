# Operating Budgets

Operating budget built on the **chart of accounts**, compared against reality with
no prior data entry.

## Why this module

Odoo Community ships no budget module. Third-party accounting kits each bundle
their own, so a database often ends up with several competing "Budgets" menus
pointing at unrelated, usually empty models. This module replaces them with one,
and hides theirs behind a setting that puts them back when you turn it off.

## The budget post is a group of ledger accounts

Not an analytic account. Analytic accounts stay available as a **second axis**,
line by line, when they are filled in. A budget therefore works on day one, even
if no vendor bill carries an analytic distribution yet.

## Four amounts, Odoo's own vocabulary

| Amount | What it reads |
|---|---|
| **Planned** | Entered, spread across the months of the exercise |
| **Actual** | Posted journal items on the post's accounts |
| **Committed** | Actual plus known commitments not yet posted |
| **Theoretical** | What should have been spent by today |

## One line reads one source, never two

A posted bill carrying an analytic distribution produces **both** a journal item
and an analytic line. Adding them up would count every dollar twice, silently,
with nothing looking wrong on screen. So each line declares its source, and the
two are disjoint by construction:

- `accounting` reads posted journal items;
- `internal cost` reads **only** analytic lines with no journal item behind them,
  which is where timesheets and manual analytic entries live.

## The theoretical follows the calendar, not the clock

An annual renewal does not spread over twelve months. The monthly split is
editable, and the theoretical is built on it. A flat time-elapsed prorata would
report an overrun every renewal month and an underrun the rest of the year, and
an alert nobody trusts protects nothing. The module shows which basis it used.

## An open budget is not edited

It is revised. A revision is a new numbered record; the original stays readable.
A budget quietly edited in place no longer measures anything, because nobody can
tell what reality was compared against.

## Coverage check

The module reports operating accounts that no post covers, and accounts covered
by two posts. Without it a budget can look respected simply because a post is
missing.

## Alerts

A line is flagged when its drift passes **both** a percentage and a currency
floor. Both, never one alone: a percentage alone screams about small posts, an
amount alone stays silent about large ones. No published practice fixes these
numbers, so the module does not invent them: they are settings.

## Hours logged but valued at zero

Odoo values timesheet time from the employee's hourly cost. When that rate is
missing — the common case on a fresh payroll setup — the amount is 0.00 while
the hours are very much there. A line would read "nothing spent" and look
perfectly normal. So the module counts those unvalued hours and shows them.

## The family

The base module works alone. Each satellite installs itself when both sides are
present and adds one thing.

| Module | Adds |
|---|---|
| `bf_budget` | The base: positions, budgets, monthly split, the four amounts |
| `bf_budget_subscription` | Recurring commitments: committed stops waiting for the invoice, and the theoretical follows the renewal calendar |
| `bf_budget_forecast` | The rolling forecast: a moving horizon, an actuals cut-off, and numbered vintages you can compare |
| `bf_budget_forecast_subscription` | Seeds forecast months from the dated calendar instead of a flat average |
| `bf_budget_campaign` | Ties a marketing campaign to an analytic account, so a campaign knows what it spent |

## Extension points

`_get_extra_commitments()` and `_get_calendar_theoretical()` on `bf.budget.line`
return nothing in the base module. `_seed_value_for()` on
`bf.budget.forecast.line` returns a flat average. Satellites fill them in with
confirmed purchase orders, approved expenses and dated subscription renewals —
and drivers, one day, without breaking anything.

## Licence

LGPL-3.
