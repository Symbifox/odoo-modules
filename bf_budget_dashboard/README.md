# Operating budgets — Dashboard bridge (`bf_budget_dashboard`)

Auto-installs when `bf_budget` and `bf_home` are both present. Adds one tile to
the Symbifox home screen that says three things:

* how much of the open budget is already committed, as a percentage;
* the number of flagged lines, in red, because it is the only figure on the
  tile you act on immediately;
* the variance against the theoretical, that is, whether spending is running
  faster than the plan at this point of the fiscal year.

One budget is named: the most recent open one, and the one the viewer is
responsible for if there is such a budget. A percentage that mixed fiscal years
or directions (expenses, revenue) would mean nothing, and the red border follows
the **named** budget, not the count: a red card above the name of a budget that
is on track states a falsehood.

The count of flagged lines, on the other hand, covers every open budget of
**every company selected in the switcher** (`env.companies`, not the default
company alone). Missing an overrun because it sits on another budget — or on
another of your companies — would be worse than having no tile at all, and the
daily digest would then contradict the home screen on the same data.

That count is paid for in queries: the socle reads actuals line by line, about
three queries each. Beyond 60 open lines the tile stops counting across budgets,
counts the named one only, and says so. A partial figure that did not admit it
would be worse than the bound.

## Who sees it

`bf.budget` is open to `bf_budget.group_bf_budget_user` (read-only) and to its
manager. The tile asks for read access on `bf.budget` and `bf.budget.line`
before computing anything. Anyone else gets no tile, and nothing is written to
the log: not having access is not a failure. Without that guard, every home
screen loaded by an ordinary employee would raise an access error that the
aggregator would read as a crash, showing "Data unavailable" and leaving a
stack trace behind each time.

The amounts themselves are computed in `sudo` **inside the socle**
(`_read_actual`), because a budget reader does not necessarily have access to
the general ledger. That is the module's design, and the tile widens nothing:
it shows a budget reader what the Budgets screen already shows them.

The section is computed inside a savepoint, so a failure here never takes the
other home-screen sections down with it, and a real failure is named in
`data["failed"]` like the other collectors.

## Languages

Labels are written in English in the source; the French ships in
`i18n/fr_CA.po`. Odoo never translates into `en_US`, the source language, so a
user set to English reads the source.

## Tests

18 tests, including the role tests played as an ordinary employee and as a
budget reader, the multi-company scope, the scan bound, and the cost of the tile
(measured at 49 to 53 queries for twelve lines; the test caps it at 70).

```
odoo -d <database> -u bf_budget_dashboard --test-enable \
     --test-tags /bf_budget_dashboard --stop-after-init --http-port=8180
```

## Changelog

### 18.0.1.0.0

- First version: the budget tile on the Symbifox home screen.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). You may run it for your
own internal business operations; providing it to third parties as a hosted,
managed or resold service requires a written agreement. This version converts
to LGPL-3.0-or-later on 2030-11-02.
