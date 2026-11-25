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
- **With Work routes** (`bf_route_expense`): the install reuses its reduced
  category and its settings, so the year is counted once, across both.
- The count is read in sudo, and only two numbers of kilometres come out of
  it. Whoever claims for an employee they may not fully see (an assistant)
  still gets the right split. Creating the expense follows the access rules of
  Expenses.

## Limits

- An expense typed by hand with the *Mileage* category is not split.
- Correcting an earlier expense does not redo the later ones. The count is
  read when the claim is made.
- Until Work routes' mileage reads these settings, both modules keep their own
  threshold and rates (copied at install); the kilometres are counted once,
  across the categories of both.

## Licence

Business Source License 1.1, see `LICENSE`.
