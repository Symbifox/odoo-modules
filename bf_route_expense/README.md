# Symbifox Work Routes: Mileage Expenses (`bf_route_expense`)

The kilometres of a route day driven with the worker's own car become a draft
expense for the worker, at the per-km rate, with the route, the date and the
customers visited. Installs itself when Work routes (`bf_route`) and Expenses
(`hr_expense`) are both installed.

> **Not tax advice.** The rates below (0.73 per km for the first 5,000 km of the
> year, 0.67 beyond) and the threshold are those the CRA and Revenu Québec
> publish for 2026. Check them with your accountant each year.

## Why

A worker who drives their own car to customers is paid back per kilometre.
The route day already knows the kilometres (odometer at the start and the
end) and the purpose of the trip (the customers visited). Typing them again in
an expense is double work and a source of errors.

## Features

- **Employee's own vehicle** (*Fleet › Vehicle*): a box on the vehicle, that
  only fleet managers can tick. Only the days driven with such a vehicle claim
  kilometres; a company vehicle gives none, its kilometres stay in the mileage
  log.
- **At the end of the day** (the worker taps *Finish the day* on the phone):
  a draft expense for the worker's employee, product *Mileage*, quantity = the
  kilometres read on the odometer, date of the day, description with the
  odometer readings and the customers visited. Nothing is reported or
  submitted for the worker: they add it to their report like any expense.
- **Two rates per calendar year**: the full rate up to the threshold (5,000 km
  by default), then the reduced rate, as the CRA and Revenu Québec allow. A
  day counts the employee's mileage expenses of the year dated before it (from
  the routes or typed by hand), refused ones excepted. A day that crosses the
  threshold gives two lines.
- **Rates in the settings** (*Settings › Work routes › Mileage expenses*): each
  rate is the cost of an expense product, *Mileage* (Odoo's) and *Mileage,
  reduced rate* (made at install from Odoo's, same unit, taxes and accounts).
  In a company in Canadian dollars, the install sets 0.67 on the reduced rate,
  and 0.73 on the full rate when it still holds Odoo's placeholder (1.00) or
  nothing; a rate already set is kept. Other currencies: the office sets both.
- **Corrections**: when the office corrects a finished day (odometer, date,
  worker, vehicle), its draft expense is redone, and so are the drafts of the
  employee's later days of the year, in date order, since the threshold may
  now fall elsewhere. A later draft the worker changed by hand is left alone,
  with a note. An expense already in a report, approved or refused is never
  changed; a note on the day says so, for the person who approves it. A
  problem in the settings (product, rate, employee) never deletes the worker's own
  draft; the draft of an employee who no longer drives the day is deleted, with a note.
- **Never in the way**: a claim that cannot be made (no employee for the
  worker, no rate) never blocks the end of the day on the phone; a note on the
  day says why. *Claim the kilometres* on the day (route managers) claims or
  redoes a day, for instance one finished before the vehicle was marked, and
  gives the reason when it cannot.

## Access

The expense is created by the module (sudo) for the worker's own employee
only. The link between an expense and a route day can only be set by the
module: an employee cannot tie an expense to a day over RPC. Route managers see
the number of expenses of a day; the expenses themselves follow Odoo's expense
rules.

## Configuration

1. *Settings › Work routes › Mileage expenses*: check the two rates and the
   threshold.
2. *Fleet*: create the worker's car and tick *Employee's own vehicle*.
3. Each worker needs an employee (*Employees*) linked to their user, in the
   company of the route.

## Taxes

The amount of an Odoo expense with a fixed cost is tax included: Odoo extracts
the purchase tax of the product from it. For an allowance, the CRA and
Revenu Québec give their own factors, each on the whole allowance (5/105 for
the GST, 9.975/109.975 for the QST): a combined GST+QST tax extracted from the
amount claims slightly less. Which tax to put on the two products, if any, is
your accountant's call.

## Known limits

- Kilometres come from the odometer only: a day without both readings claims
  nothing, even when OSRM planned its distance.
- The odometer is read when the day starts. For the CRA, the drive from home
  to the usual workplace is personal, while the drive from home straight to a
  customer is generally business: a worker who reports to the depot starts the
  day at the depot; a worker whose route starts from home starts it at home.
- A refusal, or a mileage expense typed by hand, does not redo the drafts of
  the later days; *Claim the kilometres* on a day redoes its draft.
- One price per product: when the rates change on January 1, a day of the
  previous year claimed or redone after the change takes the new rate.
- One rate pair per company. A worker in two companies has two thresholds.
- The box on the vehicle says it is an employee's own car, not whose: a replacement
  who drives it that day claims its kilometres, in their own name. The office checks the
  drafts.

## License

Business Source License 1.1, see `LICENSE`.
