{
    "name": "Symbifox: kitchen sales from another register",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "summary": "The sales of a register that is not Odoo's (Square, Lightspeed, Toast, Clover), imported "
               "from its CSV or Excel export, take the dishes' ingredients out of the kitchen stock",
    "description": """
Kitchen sales from another register
===================================

- one register per source (a dining room on Square, a bar on Lightspeed), with its warehouse;
- the sales export of that register, CSV or Excel (date, item code or name, quantity), read
  whatever its column names (English or French);
- each item of the register is matched once to its dish, or marked as not food (a gift card);
- one Kitchen consumption transfer per day: the dishes leave the stock as their ingredients;
- a file, or a day, already imported for the same register is refused; an import is cancelled
  as a whole, the food put back, to import a corrected file;
- the variance report of the kitchen shows these sales in their own column.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_kitchen"],
    "external_dependencies": {"python": ["openpyxl"]},
    "data": [
        "security/ir.model.access.csv",
        "security/sales_import_security.xml",
        "data/sales_import_data.xml",
        "views/sales_import_views.xml",
    ],
    "installable": True,
    "application": False,
}
