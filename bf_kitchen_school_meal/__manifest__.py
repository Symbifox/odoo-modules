{
    "name": "Symbifox: kitchen and school meals",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "A meal of the school menu points to its kitchen dish: its cost and allergens come from "
               "the recipe, and the meals served take their ingredients out of the stock",
    "description": """
Kitchen and school meals
========================

- a meal of the school menu points to its dish in the kitchen: its plate cost and food cost ratio
  are shown, and the dish's allergens count with the meal's own, at the order and on the portal;
- at the hour the school stops cancellations (in its time zone), the meals still ordered for the
  day leave the stock as their ingredients, in one Kitchen consumption transfer; a meal added
  later is taken at the next pass;
- a meal cancelled, or a day closed, after that hour puts nothing back (the food is cooked): the
  variance report of the kitchen moves it from consumption to losses;
- the caterer is the school's own company, in the same database.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen", "bf_school_meal"],
    "data": [
        "data/school_meal_data.xml",
        "views/school_meal_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
