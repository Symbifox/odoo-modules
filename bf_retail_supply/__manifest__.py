# -*- coding: utf-8 -*-
{
    "name": "Retail suppliers and orders",
    "summary": "The days a supplier delivers, the order guide that fills itself, the price "
               "file of the wholesaler, and what keeps coming back",
    "version": "18.0.1.0.0",
    "category": "Inventory/Purchase",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # "Other proprietary" is the value Odoo's manifest schema offers to say BUSL-1.1: it has
    # no BUSL value. The LICENSE file governs, not this line.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Retail suppliers and orders
===========================

A corner store orders from a dozen suppliers, half of them in a van on fixed days, with a
cut-off hour and a minimum order. The order is written on a sheet while walking the aisle,
and the cost of what arrives moved again since last week.

Features
--------
* On the supplier: the weekdays it delivers, the hour to order by, the minimum order, a
  van to the store or a depot to collect from, the representative, and whether it credits
  what did not sell.
* An order guide that **stays**: built over a day, aisle by aisle, it keeps what was typed
  and refreshes the facts around it. For each item: on hand, forecast, already ordered,
  the minimum and maximum of its reordering rule, the quantity suggested rounded up to the
  item's purchase unit, the supplier's price, **the cost of the last delivery and how much it
  moved**, and what came back as a credit over the last three months.
* The delivery the guide covers, and the one after: the quantity suggested covers until the
  next delivery, not until tomorrow.
* Odoo's own request for quotation, made from the guide, in the item's purchase unit, for
  the delivery day at noon in the store's own time zone.
* The price file of a wholesaler, read from its CSV or Excel whatever its column names,
  matched on the barcode then on the internal reference: each new price **opens a new
  vendor line dated from the file** and closes the one before it, so the history of the
  quoted prices is kept rather than overwritten.
* Two suppliers for the same item, read at the price per unit of the item itself: a price
  quoted by the dozen says nothing next to a shelf that sells by the unit. ⚠️ Odoo 18 holds
  ONE purchase unit per product, shared by all its vendors (a vendor line's unit is a
  related field): a supplier that sells in another pack size is modelled by its minimum
  quantity, not by a unit of its own.
* Credit notes for returns recorded per item, without touching the cost series: what did
  not sell is a reason to order less, not a cheaper unit.
""",
    "depends": ["bf_retail", "purchase_stock"],
    "external_dependencies": {"python": ["openpyxl"]},
    "data": [
        "security/bf_retail_supply_security.xml",
        "security/ir.model.access.csv",
        "data/bf_retail_supply_data.xml",
        "views/res_partner_views.xml",
        "views/order_guide_views.xml",
        "views/price_file_views.xml",
        "views/supplierinfo_views.xml",
        "views/credit_views.xml",
        "views/bf_retail_supply_menus.xml",
    ],
}
