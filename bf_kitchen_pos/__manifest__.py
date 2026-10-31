{
    "name": "Symbifox: kitchen and register",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Dishes sold at Odoo's register take their ingredients out of the kitchen stock; a dish "
               "refunded at the register is a loss, not stock back on the shelf",
    "description": """
Kitchen and register
====================

- a recipe is put on sale at the register from the recipe itself (on sale at the register,
  register categories);
- a dish sold at the register leaves the stock as its ingredients, as Odoo does for any kit;
- a dish refunded at the register: Odoo puts its ingredients back in stock; once that return
  is done, they leave again as a loss, reason Customer return (the plate came back to the
  kitchen, not to the shelf); an item sold as it is (a bottle) goes back to the stock;
- the variance report of the kitchen shows the register's sales in their own column, and a
  count warns of register orders still open.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen", "pos_mrp"],
    "data": [
        "views/mrp_bom_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
