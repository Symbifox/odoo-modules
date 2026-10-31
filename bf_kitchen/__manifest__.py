{
    "name": "Symbifox: kitchen",
    "version": "18.0.1.2.0",
    "category": "Inventory/Inventory",
    "summary": "Kitchen management for restaurants, caterers and kitchens: recipes and plate cost from "
               "the last vendor bill, kitchen counts and losses, theoretical/actual variance",
    "description": """
Kitchen
=======

Recipes and plate cost, kitchen counts and losses.

- recipes on Odoo kits (bills of materials of type kit): the dish sold is exploded into its
  ingredients when it leaves the stock, at the register or at delivery;
- each recipe line holds the net quantity on the plate and the yield (trimming loss); the
  gross quantity, the one that leaves the stock, follows;
- sub-recipes (a stock, a sauce, a dough) used in other recipes;
- ingredient cost taken from the last validated vendor bill, by bill date, brought back to
  the stock unit, net of discount and taxes, per company and per variant; the source bill
  line is one click away; a manual cost until the first bill;
- plate cost, food cost ratio, target, suggested price with the company's rounding, margin;
  a flag when an ingredient cost is too old;
- an activity to the kitchen manager when a bill pushes a dish over its target;
- bill lines with no product, from food suppliers, to attach to an ingredient (the next
  bill from that supplier is recognised);
- a "Received" button on a draft vendor bill that brings the ingredients into stock on the
  day they arrive, once per line;
- allergens declared on the ingredient, carried up to the dishes;
- printed technical sheet (with or without costs) and allergen sheet of the menu;
- kitchen storage locations, and counts that walk through them, each location applied as it is
  entered, with the variance at kitchen cost and a reminder to the kitchen manager;
- losses declared in a few taps (what, how much, why), a dish lost exploded into its
  ingredients, valued at kitchen cost;
- preparations made ahead and held in stock, recorded with "I prepared";
- a Kitchen consumption operation type for what leaves without a delivery (bridges: invoices
  with no sales order, school meals, sales imported from another register);
- the variance between two counts: bought, consumed by sales (by source), lost (by reason),
  used and made in the kitchen, and the unexplained variance, at kitchen cost, with the ten
  biggest variances and the variance as a share of purchases.

The stock valuation stays Odoo's: the module never writes the product cost.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["stock_account", "purchase_stock", "mrp", "bf_food_allergen"],
    "data": [
        "security/kitchen_security.xml",
        "security/ir.model.access.csv",
        "data/kitchen_data.xml",
        "views/product_views.xml",
        "views/mrp_bom_views.xml",
        "views/account_move_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/attach_line_views.xml",
        "views/kitchen_stock_views.xml",
        "views/kitchen_variance_views.xml",
        "report/kitchen_reports.xml",
        "report/kitchen_templates.xml",
        "views/kitchen_menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
