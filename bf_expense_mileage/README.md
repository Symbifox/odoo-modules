# Symbifox Mileage Tiers (`bf_expense_mileage`)

Kilometres claimed at the full rate up to a yearly threshold per employee, then
at the reduced rate, as the CRA and Revenu Québec allow.

> **Not tax advice.** The rates set at install (0.73 per km for the first
> 5,000 km of the year, 0.67 beyond) are the CRA's for 2026. Check them with
> your accountant each year.

## Why

Odoo prices a mileage expense with one rate: the cost of the *Mileage*
expense category, times the kilometres. The CRA publishes two, and the switch
depends on everything the employee has already claimed in the year. Nobody
keeps that count by hand.

## Features

- **Add mileage** (*Expenses › My Expenses › Add mileage*): the employee, the
  date, the kilometres and a description. Before creating anything, the wizard
  shows how the kilometres will be claimed. It counts every mileage expense
  of the employee's calendar year, whatever its date, at either rate, refused
  ones and negative drafts excepted, so a claim dated earlier but typed later
  never starts the year over. It creates one expense per rate. A claim that
  crosses the threshold gives two lines.
- **Rates in the settings** (*Expenses › Configuration › Settings ›
  Mileage*): each rate is the cost of an expense category, *Mileage* (Odoo's)
  and *Mileage, reduced rate* (made at install from Odoo's, same unit, taxes and
  accounts). In a company in Canadian dollars, the install sets 0.67 on the
  reduced rate, and 0.73 on the full rate when it still holds Odoo's
  placeholder (1.00) or nothing. A threshold of 0 keeps every kilometre at the
  full rate.
- **With Work routes** (`bf_route_expense`, 18.0.1.1.0 and later): a route
  day driven with the worker's own car is split by these same settings and
  this same count. Set the rates and the threshold once, here (the Routes
  settings show the same values). Installed alongside an older Work routes'
  mileage, the install reuses its reduced category and its settings, so the
  year is counted once, across both.
- The count is read in sudo, and only two numbers of kilometres come out of
  it. Whoever claims for an employee they may not fully see (an assistant)
  still gets the right split. Creating the expense follows the access rules of
  Expenses.

## Limits

- An expense typed by hand with the *Mileage* category is not split.
- Correcting an earlier expense does not redo the later ones. The count is
  read when the claim is made.
- With a Work routes' mileage older than 18.0.1.1.0, both modules keep their
  own threshold and rates (copied at install); the kilometres are counted
  once, across the categories of both. Upgrade it to share one set.


## Wizards are owner-scoped

Each wizard of this module carries a global record rule,
`[('create_uid', '=', user.id)]`, on read, write, create and unlink:

- `bf.expense.mileage.wizard`

A transient model is a real table, and Odoo 18 does not restrict it to its
creator on its own: without the rule, any internal user could read a
colleague's wizard until the autovacuum cron cleared it. The rule is global,
like core's own wizard rules, so administrators do not read others' wizard
rows either. (A file uploaded in a wizard before it is saved is stored as an
attachment with `res_id` 0: Odoo lets its uploader and system administrators
read it, whatever the rule.) Tests: `tests/test_wizard_owner.py`.

## Licence

Business Source License 1.1, see `LICENSE`.
