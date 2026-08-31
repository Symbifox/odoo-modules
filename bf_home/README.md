# Symbifox Home

A home screen for Odoo 18 that answers "what needs me?" instead of "which app do I click?". It replaces the application grid with a short page ordered by **who is blocked**: what waits on you, what waits on somebody else, the money, the risk.

## License

`Other proprietary` — BUSL-1.1, see `LICENSE`.

## Design rules

Four rules shaped every line of this module, and each of them was earned.

**A band with nothing to say disappears.** No tile ever displays a zero. A quiet morning produces a short screen, which is the good news, not a wall of reassuring counters nobody reads after the first week.

**Every figure carries the action that resolves it.** Each row ships the model and domain that opens the underlying list, and the test suite refuses a row without one. A number you cannot click is a dead statistic and the screen has no room for those.

**Colour must stay expensive.** Amber has to mean "this one needs you", not "a record exists". A signal that fires on all of the data is a label, not a warning: two collectors shipped in exactly that state and the band they lived in was permanently red, which reads the same as switched off.

**No hard dependency on the modules it reads.** The manifest depends on `base`, `web` and `mail`. Every collector declares what it needs through `@needs` and is skipped when the tenant does not carry it, so the module installs on a tenant with three of the source modules as readily as on one with all thirteen.

## Bands

| Band | Question it answers | Collectors |
| --- | --- | --- |
| Ce qui m'attend | What is on me today | Today's meetings, overdue activities, tasks due, unread Discussion mentions, unhandled email, unsent meeting reports, yesterday's missing timesheet |
| En attente d'eux | Where the ball is in somebody else's court | Tasks parked on a client or a third party, signature requests sent and unsigned, secure deposits about to expire, outreach targets due |
| L'argent | What leaks when nobody looks | Hour banks below the threshold they declare |
| Le risque | Compliance and operations | Consents about to lapse, services in difficulty, detractors with an open loop |

Below the bands, three read-only panels (meetings, knowledge, hosting) carry background figures. Anything that demands an action belongs in a band above, where it can be clicked and resolved.

## The figures

Below the panels sit the figure tiles: money, operations, security. They came from `bf_dashboard`, a second screen that answered the same question from the same data under a second menu. It was absorbed here on 2026-08-30 and no longer exists as a module.

Three names were kept **verbatim** through that move, and none of them is free to change:

* the model `bf.dashboard`,
* the signature of `get_dashboard_data()`,
* the OWL template name `bf_dashboard.Dashboard`.

Five modules extend them, four of those through an xpath anchored on a literal expression inside the template. An extension xpath that no longer resolves **does not raise**: the inheriting module's tile simply stops rendering, with nothing in the log. `tests/test_dashboard.py` pins both anchors so that a template edit fails a test instead of failing a screen.

The accounting collectors moved under the same `@needs` guard as everything else, so `account` and `project` are no longer hard dependencies. The guard asks whether the model and its fields exist, not how the collector reads them, which is why it covers the three blocks of raw SQL as well as the ORM calls.

`En attente d'eux` is the reason the module exists. No ERP surfaces the work that is technically not yours right now, and that is exactly where billable time evaporates.

### What "inbox" counts

Two rows, because the word means two things on an Odoo tenant and merging them produces a figure nobody can act on. **Discussion** counts `mail.message` needing action: mentions and followed records. **Courriels** counts real mail, read from `bf.email` — the mailbox mirror — never from IMAP, which Odoo cannot see without fetchmail. The email row asks `bf_email_management` for its own definition of "boîte de réception" rather than keeping a private copy, because the systray badge, the list action and the phone filter already had to agree on it, and it is scoped to the reader: an operator who may read everyone's mail should still get their own morning. On a tenant without `bf.email`, only the Discussion row appears.

## Proving silence

The first two design rules have an unpleasant consequence: a misspelled field name and an absent module look identical at runtime. The collector goes quiet either way, and nothing anywhere says so. **Four collectors shipped dormant before this was addressed.**

Three mechanisms close the three ways a collector can go silently wrong:

| Failure | How it hides | Guard |
| --- | --- | --- |
| Field does not exist | `_has()` returns False, band vanishes exactly as if the module were absent | `test_no_collector_is_dormant_by_typo` |
| Field exists but cannot go in a domain | `_has()` returns True, the domain raises inside `_safe()`, band vanishes | `test_declared_fields_are_searchable` |
| Field is declared and never used | Output looks correct forever on a tenant where the filter would be a no-op | `test_declared_fields_are_used` |

`@needs` publishes every requirement into `REQUIREMENTS`, so the tests can assert that a model which *is* installed carries every field the collector claims. `_diagnose()` answers the same question from a shell: for each collector, absent module, missing field, or active.

The third guard reads executable source only, via `ast`, with the docstring and decorators stripped. A first version grepped raw text and passed happily with the bug reintroduced, because the docstring explaining the bug named the field — it was scoring prose.

`PYTHON_FILTERED` records the collectors that knowingly declare a field they cannot put in a domain and filter in Python instead. The exemption sits in the module rather than hidden inside the test, so it stays a decision on the record.

## Thresholds belong to the source module

`_c_hour_banks` reads each bank's own `threshold_mode` and its `balance_floor` lines rather than applying a floor of its own. A bank with alerting `disabled` is left alone: somebody chose that, and a home screen is not the place to overrule it.

This matters more than it sounds. A flat ten-hour floor flagged every active bank on the reference tenant, including ones that had alerting explicitly switched off — the band was permanently red and therefore mute. Modes this row cannot render as a balance (`budget_pct`, `unbilled`) keep a default floor rather than dropping into silence, and the subtitle says which reading applied.

## Time zones

Datetime columns are stored and compared in UTC. Building a day window from a naive `date.today()` therefore asks for a *UTC* day, which in Montreal starts at 20:00 the previous evening. `_day_bounds()` converts the reader's calendar day through their timezone first, and `_days_since()` counts between the reader's days, not the server's.

The trap is that the display side already went through `context_timestamp`, so an 8 p.m. meeting rendered as "Aujourd'hui à 20 h 00" — on the wrong day, in plausible-looking prose.

## Landing page

`data/bf_home_data.xml` sets `res.users.action_id` as an `ir.default`, so users created after installation land here instead of the application grid. It is written as a default rather than onto existing accounts on purpose: a home action is a personal setting, and a default is undone from Settings without touching the module.

Installing the module without this only added a menu; the premise went unshipped.

## Failure behaviour

- One broken collector costs its band, never the page. Each is wrapped in `_safe()`, which logs and yields nothing rather than raising into the client action.
- `bf.home` is an `AbstractModel`, so it has no table and carries no `ir.model.access` row. Every collector reads through the calling user, so record rules apply and a user without rights on a model simply gets that band omitted.
- When the RPC call itself fails, the screen says so and offers a retry instead of rendering a blank page that looks like a quiet morning. The message is read from `e.data.message`, which is where `makeErrorFromResponse()` puts the server's sentence — `e.message` is the envelope string `"Odoo Server Error"`.

## Performance

`get_home_data` runs in roughly 0.3 s on a tenant carrying 258 meeting records, 43 monitored services and a 300-task backlog. Availability is read from the stored verdict on each service, never by aggregating `hosting.health.check`: that table carries over a million rows and this screen loads on every login.

## Tests

```bash
odoo -d <db> -u bf_home --test-enable --test-tags /bf_home --stop-after-init
```

The suite asserts the contract the client action relies on, not the figures, which depend on the tenant and on what happened yesterday: the call succeeds, the shape is stable, empty bands are absent rather than zeroed, every row can be opened, and one broken collector cannot take the screen down.
