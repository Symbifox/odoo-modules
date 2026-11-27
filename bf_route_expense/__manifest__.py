{
    "name": "Symbifox Work Routes: Mileage Expenses",
    # 18.0.1.1.0: the settings and the split of the two rates are Mileage Tiers' (one set per
    # company); the bf_route_mileage_* fields are migrated, then removed.
    "version": "18.0.1.1.0",
    "category": "Human Resources/Expenses",
    "summary": "The kilometres of a route day driven with the worker's own car become a draft "
               "expense at the per-km rate",
    "description": """
Mileage expenses for work routes
================================

Installs itself when both Work routes and Expenses are installed, with
Mileage Tiers (bf_expense_mileage), whose settings and split it uses.

- a vehicle can be marked as the employee's own car. A route day finished with
  it gives a draft expense to the worker: the kilometres read on the odometer,
  at the per-km rate, with the route, the date and the customers visited;
- two rates per calendar year, as the CRA and Revenu Québec allow: the full
  rate up to a number of kilometres (5,000 by default), then the reduced rate.
  The rates, the threshold and the count of the year are those of Mileage
  Tiers (Expenses settings, Mileage; shown in the Routes settings too): route
  days, Add mileage and trips share one count per employee;
- the worker reports and submits the expense like any other. A company vehicle
  gives no expense: its kilometres stay in the mileage log;
- an odometer corrected by the office redoes the draft; an expense already
  reported is never changed, a note on the day says so.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_route", "hr_expense", "bf_expense_mileage"],
    "data": [
        "views/fleet_vehicle_views.xml",
        "views/bf_route_day_views.xml",
        "views/hr_expense_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    # Still installs itself with Work routes and Expenses, and brings Mileage Tiers along.
    "auto_install": ["bf_route", "hr_expense"],
}
