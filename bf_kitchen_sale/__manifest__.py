{
    "name": "Symbifox: kitchen and sales",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "Dishes sold on quotations and invoices take their ingredients out of the kitchen stock, "
               "an invoice with no sales order too",
    "description": """
Kitchen and sales
=================

- dishes quoted and sold on sales orders (caterers, banquets): the delivery takes the
  ingredients out of the stock, as Odoo does for any kit;
- a customer invoice with no sales order (a banquet invoiced directly) takes them out when it
  is posted, in one Kitchen consumption transfer, and puts them back when it is reset to
  draft or cancelled;
- an invoice of a sales order or of the register is never counted twice;
- the variance report of the kitchen shows these sales in their own column.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen", "sale_mrp"],
    "data": [
        "views/account_move_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
