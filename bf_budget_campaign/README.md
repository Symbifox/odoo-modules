# Operating Budgets — Campaigns (`bf_budget_campaign`)

Attaches a campaign to an analytic account. The campaign already knew what it
earned; it finally knows what it spent.

## The problem

Odoo already knows what a campaign **returned**. `utm.campaign` carries the
invoiced amount, the lead count and the quotation count, all computed.

None of its thirty-three fields is a cost. No spend, no budget, no analytic
account. The campaign therefore measures a return whose denominator it does not
have.

## What this is not

It is **not** a spend register. Such a register would duplicate the accounting,
and the two amounts would eventually differ.

A campaign already exists in Odoo, and the editorial workshop already points at
one: an editorial entry and a calendar both carry a campaign field. What was
missing was never the campaign, nor the expense. It was the link between them.

## What it adds

- **One analytic account per campaign**, created from a button, with a
  uniqueness constraint: an account never serves two campaigns, otherwise both
  would display the same spend.
- **The campaign spend**, split between what went through accounting and what is
  internal time.
- **Unvalued hours**, flagged. Odoo values time from the employee cost rate;
  when that rate is missing the campaign reads zero and looks perfectly normal.
- **The budget lines** whose analytic axis names that account, with their
  cumulative plan.

The expense itself comes in through the normal accounting path: a supplier
invoice or an expense report carrying its analytic distribution. It lands in the
ledger, sales taxes included, with no double entry.

## Hours nobody priced

Odoo values a timesheet from the employee cost rate. Where that rate is not set,
the line is worth $0.00 while the hours are plainly there, and the campaign reads
as free. Flagging that is necessary but not sufficient: a warning tells you a
number is missing, it does not tell you how big it is.

So the module carries a **default hourly cost**, set per instance under Settings
in the Budgets section, and uses it to price the hours Odoo could not.

The estimate never touches the real figure:

- `Total spend` stays what the books say, and nothing else.
- `Estimated internal cost` and `Estimated total spend` live in their own fields,
  in their own section, shown only when there are unpriced hours.

A test asserts the two totals differ whenever an estimate exists, because an
estimate that quietly merges into a booked figure is worse than no estimate.

The default is $50.00. If the setting has never been opened, that default still
applies: an absent system parameter reads as `False`, and `float(False)` is
`0.00`, which would have silently priced every hour at nothing.

## One read, one source

This module inherits the rule that protects the figure in `bf_budget`, and
applies it unchanged.

A posted invoice carrying an analytic distribution produces **both** a journal
entry and an analytic line. Adding the two would count every dollar twice, in
silence, with nothing looking wrong on screen. The split here is therefore made
on a fact that cannot be true of both sides at once:

- **Accounted spend** — analytic lines that carry a `move_line_id`, meaning they
  are backed by a journal entry.
- **Internal cost** — analytic lines with **no** `move_line_id`: timesheets and
  hand-entered analytic lines.

The two sets are disjoint by construction, and the total is their sum. A test
asserts that identity rather than trusting it.

## The campaign plan sits under the project plan

This detail is not cosmetic.

Odoo stores analytic lines in **one column per root plan**: `account_id` for the
project plan, `x_plan<id>_id` for any other root plan. A "Campaigns" plan created
beside the project plan instead of under it would produce campaigns whose spend
stays at zero, with no error, and nothing would distinguish that zero from a
campaign that spent nothing.

The module creates its plan as a **child** of the project plan, and a constraint
refuses an account whose root plan is another one. A refusal at write time beats
a repair at read time.

The plan is created on demand, at the first account, rather than by an install
hook: a hook that fails blocks a fresh installation.

## Advertising APIs

The module pulls no spend from an advertising API, and that is not an oversight.

The publishing connectors already in place speak to **publishing** APIs. Pulling
spend would need the **Ads** APIs, which are different products, with their own
permission scopes, their own access review and their own manager account.
Counting an existing connector as progress on that work would be an estimation
error.

Normal accounting entry therefore stays the path, and the fallback: an
advertising access can be refused or revoked, and budget tracking does not get to
stop for that.

## Things worth knowing

**Base `utm.campaign` has neither `company_id` nor `currency_id`.** Both arrive
with `sale`. A `Monetary` field inheriting the default `currency_field` makes
`registry.setup_models` fail outright — the module will not install at all on a
database without `sale`. This module carries its own currency instead.

## The figure is a sum, stable by role; the detail is what closes (v18.0.1.2.1)

A campaign's spend is now read with elevated rights, like every other figure in
the budget family. Before, it was read with the viewer's own rights, and that
broke it two ways, both measured: a user with no analytic rights — a plain
internal user, or a marketing user — **crashed** when opening a campaign with an
analytic account, because reading campaigns is granted to every internal user
while reading analytic lines is not; and a user with accounting read rights was
filtered by Odoo's own rule to lines with no project, so their internal cost and
hours read **too low, in silence, with no error at all**. The same applied to the
budget lines behind *Planned*.

**The counterpart: a correct figure is not a public one.** A campaign's internal
cost is made of timesheets valued at each employee's hourly rate — aggregated
salaries. So the cost and hours fields are restricted to the roles that could
already read analytic lines company-wide (accounting read, accounting invoice,
analytic accounting) plus the budget reader, since this is a budget figure; and
the *Planned* / budget-line fields are restricted to the group that reads budget
lines. The restriction sits on the **fields**, not only on the screen: a field
hidden from a view still answers an RPC `read`. Roles whose analytic view was
filtered to their own timesheets — timesheets, project manager, purchase, sales,
expense approver — are deliberately left out: the number they used to see was
never right, and handing them the company's true labour cost would be a new
exposure, not a fix. The campaign's analytic account itself stays open: it is the
marketing team's own entry, not a cost.

**One more consequence of elevated rights:** the company now sits in the
measure's domain, because elevated rights bypass the global multi-company rule
as well. On an account that belongs to a company, Odoo itself already refuses to
file another company's analytic line against it, so that filter is a belt on top
of braces rather than a guard. On a **shared** account — one with no company —
there is deliberately no filter: the total is the account's total across
companies, the same figure for every reader, instead of depending on which
companies the reader is allowed to see.

## Opening the detail needs the right to read analytic lines (v18.0.1.2.1)

A campaign's internal cost is made of timesheets, valued from the employee's
hourly rate: unfolding a campaign is being able to read salaries. Reading
`utm.campaign` is granted to every internal user; reading
`account.analytic.line` is not — it comes from the timesheet, analytic
accounting, purchase or invoicing groups. The *Spend* button's action therefore
refuses first to anyone without that right, instead of handing back a window
that raises a bare access error at render time. It passes a **domain**, never
records fetched with elevated rights, so record rules still apply to each
viewer. Same guard, same shape, as `bf_budget_analytic`.

## What it does not do

- Campaign totals cover its whole life. It is the budget line that carries a
  period; the campaign has none, and inventing one would duplicate the decision.
- It shows spend, not return. The invoiced amount stays where Odoo computes it.

## Running the tests

```
odoo -d <db> -u bf_budget_campaign --test-enable --test-tags /bf_budget_campaign \
     --stop-after-init --http-port=8899
```

## Dependencies

`bf_budget`, `utm`.

## Languages (v18.0.1.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The analytic plan for campaigns is created with its name in every installed language, rather than in the language of whoever created the first campaign account.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-10 for this one. In the meantime, use in production for
your own internal business operations is permitted.
