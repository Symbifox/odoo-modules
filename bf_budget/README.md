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
numbers, so the module does not invent them: they are settings. New budgets
take the instance defaults (10 % and 250 by default, under Settings > Budgets),
and each budget keeps its own.

Each budget has a **responsible**. A daily scheduled job tells them when a line
**crosses** the threshold, once: a note in the budget's chatter and an activity
assigned to them, in their language. The line does not alert again while it
stays flagged. It is re-armed when it stops being flagged, either because
spending falls back under the threshold or because the variance is accepted on
the line, with its reason. The activity closes by itself once no line is
flagged, or when the budget is no longer open.

The activity goes only to a responsible who can read the budget (active,
internal, in the budget's company, with access to budgets). Otherwise the note
says that nobody was assigned, the budget form shows a warning, and the alert
reaches the responsible as soon as they can be told.

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
| `bf_budget` | The base: positions, budgets, monthly split, the four amounts, alerts |
| `bf_budget_subscription` | Recurring commitments: committed stops waiting for the invoice, and the theoretical follows the renewal calendar |
| `bf_budget_forecast` | The rolling forecast: a moving horizon, an actuals cut-off, and numbered vintages you can compare |
| `bf_budget_forecast_subscription` | Seeds forecast months from the dated calendar instead of a flat average |
| `bf_budget_campaign` | Ties a marketing campaign to an analytic account, so a campaign knows what it spent |
| `bf_budget_expense` | Approved expense reports enter committed before they are posted; submitted ones are shown apart, in no amount |

## Extension points

`_get_extra_commitments()` and `_get_calendar_theoretical()` on `bf.budget.line`
return nothing in the base module. `_seed_value_for()` on
`bf.budget.forecast.line` returns a flat average. Satellites fill them in with
confirmed purchase orders, approved expenses and dated subscription renewals —
and drivers, one day, without breaking anything.


## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The budget position is labelled **Budget item** in English, as in the rest of the family, so it no longer reads like a budget line. The positions the wizard proposes and the type shown on each report row are read in the user's language.

## Alerts that reach someone (v18.0.1.2.0)

A responsible per budget, told once when a line crosses the threshold (a chatter note and an activity), re-armed when the line stops being flagged. Default thresholds are set per instance under Settings. See *Alerts* above.

## A revision keeps its breakdown, and an open plan stays frozen (v18.0.1.2.1)

Found in production on 2026-10-31, on the first real alert of a live budget:
"Create a revision" copied the monthly breakdown and then overwrote it. A one-off
expense booked in May ended up spread over twelve months in the revision, and the
line was flagged although nothing had changed in the books.

`amount_planned` is a **stored, writable computed** field (it has an inverse, so
that typing a total spreads it). Odoo only forces `copy=False` on a computed
field that is readonly, so the copy carried the total as well as the periods, and
the inverse re-spread them on create. The field is now `copy=False`: a copy takes
the breakdown, and the total follows from it. A line with no period at all keeps
its total, through `copy_data`.

Closing that door showed two others, both now shut:

* the freeze only held on `bf.budget.write`. The standalone line form, the one
  you open from an alert, let you retype `amount_planned` on an **open** budget,
  and retyping the same total was enough to re-spread the breakdown. Writing the
  plan is now refused server-side as well, in `bf.budget.line.write` and
  `bf.budget.line.period.write`, and `action_spread_evenly` refuses outside a
  draft like `action_regenerate_periods` already did. Follow-up stays open on an
  open budget: accepted overrun, its reason, the alert memory, followers.
  ⚠️ In 18.0.1.2.1 the guard covered `write` only; v18.0.1.3.0 below closes
  `create`, `unlink`, `copy` and the remaining paths.
* a revision inherited `overrun_accepted` and its reason. The module already
  clears the crossing memory on copy so that a new crossing alerts again, and it
  was copying what **prevents** alerting: a decision taken against the old plan
  silenced the lines of the new one. Both fields are now `copy=False`.

## The freeze is now complete server-side (v18.0.1.3.0)

Every remaining path that rewrote the plan of a budget past the draft stage is
refused server-side, whatever the entry point (form, RPC, import, context
defaults):

* a line can no longer be **created**, **duplicated** (one click on the line
  form) or **deleted** on such a budget, nor **moved** from a draft into one;
* a monthly period can no longer be created, deleted, **re-dated** (the dates
  carry the theoretical, hence the alert) or re-parented onto such a budget;
* sliding `state` into the same `write` no longer skips the budget guard. The
  only combined write that passes is the one that **resets to draft while
  editing** — and only from the states `action_reset_draft` itself accepts
  (open, cancelled): a closed or revised budget does not come back that way;
* the `create` guards are evaluated on the **created records**, after the ORM
  has injected context defaults — a guard that reads the raw values never sees
  `default_line_id` / `default_budget_id` come in;
* a budget item (`bf.budget.position`) of **another company** than the budget
  is refused on the line, and re-companying a position used by living lines is
  refused on the position.

**Re-dating a draft regenerates its breakdown.** A revision re-dated to the
next fiscal year used to keep the OLD year's periods: the total stayed right
while the curve was a year off. Now, when a draft budget's dates change, each
line's periods are regenerated; the breakdown is carried over **by month
position** when the number of months is unchanged (a May expense stays in the
fifth month), and spread evenly otherwise — in which case the budget **says so
in its chatter**, because a silent re-spread is a plan that lies with a correct
total. ⚠️ Change both dates in a **single** `write` (what the form does): two
successive writes go through an intermediate grid and lose the hand-tuned
seasonality — the chatter note is the trace when that happens. Changing the
dates and leaving the draft state in the **same** write is refused: redate
first, then open.

**Known limits, by design:**

* the freeze covers the budget's own plan. The referential it points at is
  shared and stays alive — editing the **accounts of a budget item** changes
  what every budget reading it measures, including open ones. Freezing
  positions would block legitimate chart-of-accounts evolution; review
  position changes through ordinary access control instead;
* `state` itself is not a frozen field: a bare `write({"state": "draft"})`
  (RPC, import) resets a closed or revised budget without going through
  `action_reset_draft`, and the plan becomes editable again in a second call.
  The transition is tracked in the chatter, which is the trace; guarding the
  state machine itself is a different undertaking than freezing the plan.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-04 for this one. In the meantime, use in production for
your own internal business operations is permitted.
