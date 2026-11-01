{
    "name": "Symbifox: host stand",
    "version": "18.0.1.0.0",
    "category": "Sales/Point of Sale",
    "summary": "The host seats the guests on the register's floor plan: sections by server in turn, waiting "
               "list with a quoted wait, and the kitchen's pace in view",
    "description": """
Host stand
==========

- a host logs in to Odoo's register with a PIN and sees the floor plan only: seat a party (covers,
  name, note), move it, mark the table cleared; never an order nor a payment;
- each table shows where it is: free, seated, ordered, served, to clear;
- the floor is split in sections, one server each for the service; the next section in turn is
  proposed, weighted by the covers already seated, with a warning when a section was just seated;
- a waiting list quotes the wait from the real length of meals at this register and the state of
  the tables;
- a pace gauge shows the covers seated in the last 15 minutes and the kitchen tickets still open;
- the guest's note (an allergy) reaches the order, then the kitchen; names and notes are erased
  when the session closes, from the order and the kitchen tickets too, and never reach an invoice.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen_display", "pos_hr", "pos_restaurant"],
    "data": [
        "security/ir.model.access.csv",
        "security/host_security.xml",
        "data/host_cron.xml",
        "views/host_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "bf_kitchen_host/static/src/pos/**/*",
        ],
    },
    "installable": True,
    "auto_install": False,
    "application": False,
}
