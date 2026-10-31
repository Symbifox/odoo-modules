# Symbifox: kitchen and sales (`bf_kitchen_sale`)

Bridge between the kitchen (`bf_kitchen`) and Odoo's sales: dishes sold to caterers' and
banquets' customers take their ingredients out of the kitchen stock, once. Installed on its
own when `bf_kitchen` and `sale_mrp` are both there.

## Features

- **Sales orders.** A dish quoted and sold on a sales order leaves with its delivery: Odoo
  (`sale_mrp`) explodes the kit into its ingredients. Nothing is rewritten.
- **Invoice with no sales order.** A customer invoice posted with kitchen items and no sales
  order (a banquet invoiced directly) takes them out of the company's kitchen warehouse (kitchen
  settings; the first warehouse when none is set) in one Kitchen consumption transfer,
  dated of the invoice, linked to it (a "Kitchen" button on the invoice). Reset to draft or
  cancelled, the invoice puts them back; posted again, they leave again, once.
- **Corrected by credit note.** The credit note's "modify" mode (reverse and make a new draft)
  puts the food of the first invoice back; the corrected invoice takes its own quantity out when
  it is posted.
- **Never twice.** A line of a sales order is left to its delivery (an order invoiced on
  ordered quantities is invoiced before it is delivered); an invoice of Odoo's register is left
  to the register's own transfer. A credit note takes nothing out and puts nothing back.
- **Never blocks.** A consumption that fails leaves the invoice posted, with the reason in a
  note on the invoice.
- In the kitchen's variance report, these sales have their own column ("Invoice without a
  sales order", "Sales orders").

## What the module does not do

A plain credit note for a dish is a commercial gesture: the food was served, nothing comes back. An
invoice is taken out at the moment it is posted: one posted after a count for a day before it
is shown by the variance report.

## Requirements

Odoo 18 with Sales and its kits (`sale_mrp`), and `bf_kitchen`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
