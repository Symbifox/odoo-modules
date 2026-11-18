# -*- coding: utf-8 -*-
{
    "name": "Retail prices",
    "summary": "The cost of what a store buys, kept as a series, and the shelf price that follows it",
    "version": "18.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # "Other proprietary" is the value Odoo's manifest schema offers to say BUSL-1.1: it has
    # no BUSL value. The LICENSE file governs, not this line.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Retail prices
=============

A corner store, a dairy, a small grocery buys to resell. Its cost moves every week and
its margin is made of cents. This module keeps the cost as a **series** instead of a
single number, and makes the shelf price answer to it.

Features
--------
* Retail categories: the categories followed, their target margin and their price
  rounding, inherited by the categories below them.
* A cost series per product and per company: one row per cost observed, with its date,
  where it comes from (a validated vendor bill, a purchase order, a price file, a hand
  entry), the supplier, the bill line one click away, and the change against the
  previous cost in amount and in percent.
* A validated vendor bill or purchase receipt writes the series by itself, in the
  company's currency, net of discount and taxes, per unit of the product's own unit of
  measure, and one row per product: the lines of a product are added up, rebates
  included, so the cost is what was really paid per unit. A bill that goes back to draft
  or is cancelled takes its rows back out.
* On the product: the current cost and its age, the margin, the suggested price for the
  target margin, and the gap against the price on the shelf.
* The margin is always computed **excluding tax**, from the taxes of the sale: a shelf
  price that includes GST (New Zealand) and a price that excludes the sales taxes
  (Quebec) give the same margin.
* Price changes in batches: a batch proposes every product whose price has drifted from
  its target, holds the cost and the two margins, and refuses, by name, a product whose
  price or cost moved since the proposal, a product shared with another store that
  follows the retail (the shelf price is not kept per company in Odoo), and a variant
  whose price cannot be moved without moving its sister variants.
* A shelf label queue: what has changed price and has not been reprinted, and Odoo's own
  label layout to print it.
* A journal of every move of a followed product's shelf price, from a batch or typed on
  the product.
* The cost against price index: per category and over a period, how much the cost moved,
  how much the shelf price moved, and the spread between the two, read from that journal
  so that a rise passed by hand counts like one passed by a batch.

The module never writes the stock valuation cost: it reads the bills and keeps its own
series.
""",
    "depends": ["account", "mail"],
    "data": [
        "security/bf_retail_security.xml",
        "security/ir.model.access.csv",
        "data/bf_retail_data.xml",
        "views/retail_cost_views.xml",
        "views/product_views.xml",
        "views/price_change_views.xml",
        "views/label_queue_views.xml",
        "views/retail_index_views.xml",
        "views/res_config_settings_views.xml",
        "views/bf_retail_menus.xml",
    ],
}
