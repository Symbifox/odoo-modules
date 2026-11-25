# -*- coding: utf-8 -*-
{
    "name": "Business Trips",
    "summary": "A business trip gathers its receipts, its meal allowances, its mileage and its "
               "hotel folio into one expense report, and an assistant can prepare it",
    # 18.0.1.0.1: the module icon.
    "version": "18.0.1.0.1",
    "category": "Human Resources/Expenses",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » is the manifest schema's value for BUSL-1.1: the LICENSE file
    # governs, not this line.
    "license": "Other proprietary",
    "description": """
Business trips
==============

Odoo knows one expense at a time. A trip is a dozen of them (the flight, the
hotel, the meals, the taxi, the kilometres to the airport), paid over several
days, often in two currencies, and very often put together by someone other
than the traveller: the administrative or executive assistant.

The trip
--------

A trip has a traveller, dates, a destination, a purpose, and optionally a
customer and an analytic distribution.

- **Collect receipts**: the traveller's expenses dated within the trip, not
  yet in a report, are attached to it. An expense created while a single trip
  of the traveller covers its date is attached on its own;
- **Create report**: one expense report for the whole trip. The expenses
  without an analytic distribution take the trip's;
- **Add mileage**: the kilometres split between the full and the reduced
  rate of the year (Mileage Tiers).

Allowances
----------

One line per day of the trip: breakfast, lunch, dinner, incidentals, and a
night in private lodging (with friends or family, no hotel receipt). Untick a
meal that was provided (a conference lunch, a meal on the plane).
**Generate allowances** turns the ticked boxes into expenses, at the company's
rates. The rates are the company's own, with a start date, in Expenses,
Configuration, Allowance rates; the federal government's rates (National
Joint Council, October 1, 2026) can be loaded as a starting point. The module
does not decide whether an allowance is reasonable: that is the employer's
policy, and what makes it taxable or not.

Assistants
----------

On the employee form, **Travel assistants** names the users who prepare this
employee's trips and expenses. An assistant can create, attach, split and
submit them on the traveller's behalf, and **cannot approve them**, even when
the assistant is an expense approver otherwise.

Hotel folio
-----------

A hotel bill carries several things in one total: the room, the lodging tax,
meals, parking, and sometimes personal charges (minibar, a movie).
**Split hotel folio**, on a draft expense, splits it into one expense per line
with its own category and taxes, the receipt copied on each; the personal
lines are not reimbursed, and the amount set aside stays written on the
expense.

What it does not do
-------------------

Book travel; pay advances; compare the trip with a budget; claim the input
tax credits on meals at 50 %. These belong to the next lots or to the
company's tax file.
""",
    "depends": ["hr_expense", "bf_expense_mileage"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_expense_trip_security.xml",
        "data/product_data.xml",
        "wizard/bf_expense_folio_wizard_views.xml",
        "wizard/bf_expense_mileage_wizard_views.xml",
        "views/bf_expense_trip_views.xml",
        "views/bf_expense_allowance_rate_views.xml",
        "views/hr_expense_views.xml",
        "views/hr_employee_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
