# -*- coding: utf-8 -*-
{
    "name": "Mileage Tiers",
    "summary": "Kilometres at the full rate up to a yearly threshold per employee, then at the "
               "reduced rate, as the CRA and Revenu Québec allow",
    # 18.0.1.0.1: the module icon.
    # 18.0.1.0.2: the Add mileage wizard is owner-scoped (record rule on its table).
    # 18.0.1.0.3: the count of the year takes expenses to leave out (Work routes' mileage reads
    # these settings and this split, and redoes its days in order).
    "version": "18.0.1.0.3",
    "category": "Human Resources/Expenses",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » is the manifest schema's value for BUSL-1.1: the LICENSE file
    # governs, not this line.
    "license": "Other proprietary",
    "description": """
Mileage tiers
=============

Odoo prices a mileage expense with one rate: the cost of the Mileage expense
category, times the kilometres. The CRA and Revenu Québec publish two: a full
rate for the first 5,000 km an employee drives for work in a calendar year,
and a reduced rate beyond (0.73 $ and 0.67 $ per km in 2026).

This module holds the two rates and the threshold once, per company, and
splits the kilometres between them:

- **Add mileage** (Expenses, My Expenses): the employee, the date, the
  kilometres and a description. The wizard counts the employee's mileage
  expenses of the calendar year, whatever their date (refused ones excepted), and
  creates one expense at the full rate, one at the reduced rate, or both when
  the claim crosses the threshold;
- the rates are the costs of two expense categories, Mileage and Mileage,
  reduced rate, set in the Expenses settings. A threshold of 0 keeps every
  kilometre at the full rate.

Work routes' mileage (bf_route_expense) reads these settings and this split
for the kilometres of a route day: one threshold, one pair of rates, one count
of the year.

What it does not do: change an expense typed by hand with the Mileage
category, or redo earlier expenses when a later one is corrected. The count
is read when the wizard creates the claim.
""",
    "depends": ["hr_expense"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_wizard_owner_rules.xml",
        "views/res_config_settings_views.xml",
        "wizard/bf_expense_mileage_wizard_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
