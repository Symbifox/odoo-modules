# Operating budgets — Daily digest bridge (`bf_budget_digest`)

Auto-installs when `bf_budget` and `daily_todo_digest` are both present. Adds a
"Budget" section to the daily digest: the flagged lines of the open budgets the
recipient is **responsible for**, with the planned, committed and theoretical
amounts and the variance, worst variance first.

## Only to someone who may read the budget

Being named responsible does not grant the right to read: `user_id` only carries
a field domain, which an import, a script or an RPC write ignores, and a person
can lose the Budgets group afterwards. The socle has a guard for exactly this
(`_alert_recipient`: active, internal, of the budget's company, member of
`bf_budget.group_bf_budget_user`) and refuses to assign the activity when it
fails. This section goes out **by email**, so it asks the same question first:
everything that enters that HTML leaves the organisation.

## The section follows the responsible, exactly like the alert does

`bf_budget` raises an activity **once**, when a line crosses the threshold, and
assigns it to the budget's responsible. This digest tells the same person,
every morning, as long as the line stays flagged. That is the division of
labour: the activity is the event, the digest is the standing reminder. Sending
it to everyone who can read budgets would turn a signal into noise, and nobody
would own it.

A quiet day stays quiet: no flagged line, no section. A closed or draft budget
says nothing — only open budgets carry a daily gesture. An accepted overrun,
with its reason, is silent here as it is everywhere else.

Beyond ten lines the table stops and says how many were left out, so the figure
is never silently truncated.

The switch is `include_budget` on the digest template (Widgets tab), on by
default.

## Implementation notes

* `is_alert` is computed and not stored, so it cannot be filtered in SQL. The
  socle offers `_search_is_alert`, which walks every line of every open budget
  — too wide for a digest. This bridge reads the lines of *this* person's
  budgets and filters them in Python, which stays bounded by what they own.
* The amounts and percentages are formatted in the recipient's language, like
  the rest of the digest. Only the language changes: the records are read in
  `sudo` after a search already restricted to the recipient's own budgets.
* ⚠️ The `<!-- Divider -->` marker of the host template sits **between** two
  `<tr>` of the wrapper table. A bare section inserted there is pushed out of
  the table by any HTML5 parser (foster parenting) and renders above the card,
  so the block carries its own `<tr>`. `_splice_budget_section` is a pure
  helper for exactly that reason: it is tested without rendering the host
  digest.

## Languages

Labels are written in English in the source; the French ships in
`i18n/fr_CA.po`. Odoo never translates into `en_US`, the source language, so a
user set to English reads the source.

## Tests

16 tests, including a responsible who cannot read the budget (without the
Budgets group, and archived) and the language of the section.

```
odoo -d <database> -u bf_budget_digest --test-enable \
     --test-tags /bf_budget_digest --stop-after-init --http-port=8180
```

## Changelog

### 18.0.1.0.0

- First version: the "Budget" section of the daily digest.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). You may run it for your
own internal business operations; providing it to third parties as a hosted,
managed or resold service requires a written agreement. This version converts
to LGPL-3.0-or-later on 2030-11-02.
