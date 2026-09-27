{
    "name": "Symbifox École : repas",
    "version": "18.0.1.0.0",
    "category": "Education/School",
    "summary": "Caterer menus, meal orders on the family portal, prepaid balance or monthly invoice, "
               "food allergies checked at the order and on the kitchen list",
    "description": """
Meals
=====

- menus: the caterer's meals with their price and allergens, one menu per school day
  (calendar);
- family portal (Meals): order for each child up to the school's deadline (2 days ahead
  by default), cancel until the hour set on the day (8:00 by default);
- payment, as the school decides, or the family chooses:
  prepaid balance: topped up by an invoice paid online or recorded by the office; a
  meal is taken from the balance when ordered and given back when cancelled;
  monthly invoice: the meals of the month on one invoice, sent at the start of the
  next month;
- food allergies: Health Canada's priority allergens; a meal containing a student's
  declared allergy cannot be ordered, and the kitchen list shows every allergy;
- closed day (storm, closure): every order is cancelled and credited at once;
- the balance is a register: movements are never edited, the office records an
  adjustment with its reason.

Meals served to students at school are exempt from GST and QST: invoice lines carry no
tax. The school's accountant confirms before going live.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_school_portal", "account_payment"],
    "data": [
        "security/ir.model.access.csv",
        "data/meal_data.xml",
        "views/meal_views.xml",
        "views/portal_templates.xml",
    ],
    "installable": True,
    "application": False,
}
