{
    "name": "Symbifox Work Routes: Sales on the Road",
    "version": "18.0.1.0.3",
    "category": "Services/Field Service",
    "summary": "Sell from the truck at each stop: truck stock, invoice at the customer's price, "
               "container deposits, payment recorded on the phone",
    "description": """
Sales on the road
=================

For routes that sell what they deliver: water, propane, supplies.

- each vehicle has its own stock location: the truck is loaded in the morning
  (transfer from the warehouse) and unloaded at night (what is left goes back);
- each stop lists the customer's usual products and quantities; the worker
  enters what was delivered and the empty containers taken back;
- marking the stop done delivers from the truck, invoices at the customer's
  price, charges the deposit on full containers and credits the empties, and
  keeps each customer's balance of containers;
- payment: cash, cheque, or card on the worker's own payment terminal. The
  phone records the amount, the method and the authorization number; no card
  data ever reaches the software. "On account" leaves the invoice open;
- at the end of the day, the cash to hand in.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_route", "stock", "account"],
    "data": [
        "security/bf_route_sale_groups.xml",
        "security/bf_route_sale_security.xml",
        "security/ir.model.access.csv",
        "views/bf_route_sale_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_route_sale/static/src/ma_route/ma_route_sale.js",
            "bf_route_sale/static/src/ma_route/ma_route_sale.xml",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
