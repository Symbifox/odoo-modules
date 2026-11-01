{
    "name": "Symbifox: kitchen display",
    "version": "18.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "The dishes a waiter sends from the register reach a kitchen screen, station by station: to do, "
               "in progress, ready, served",
    "description": """
Kitchen display
===============

- a waiter takes the order at the table on Odoo's register and presses Order: the dishes reach the
  kitchen screen, on the station that makes them (hot, cold, bar: by register category);
- each ticket shows its table, its covers, who sent it, how long ago, the dishes with their notes
  and allergens; a dish added later is a new ticket, a dish taken off shows struck out in red;
- the cook moves a ticket along: to do, in progress, ready, served;
- a table moved to another one keeps its tickets; an order deleted after it was sent shows as
  cancelled;
- the screen never takes anything out of the stock: the register does it at payment.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen_pos", "pos_restaurant"],
    "data": [
        "security/ir.model.access.csv",
        "security/display_security.xml",
        "views/station_views.xml",
        "views/ticket_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_kitchen_display/static/src/display/**/*",
        ],
        "point_of_sale._assets_pos": [
            "bf_kitchen_display/static/src/pos/**/*",
        ],
    },
    "installable": True,
    "auto_install": False,
    "application": False,
}
