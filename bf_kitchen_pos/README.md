# Symbifox: kitchen and register (`bf_kitchen_pos`)

Bridge between the kitchen (`bf_kitchen`) and Odoo's register (Point of Sale): the dishes
sold at the register take their ingredients out of the kitchen stock, and a dish refunded is a
loss, not stock back on the shelf. Installed on its own when `bf_kitchen` and `pos_mrp` are
both there.

## Features

- **On sale at the register.** The kitchen manager puts a dish on sale at the register, with
  its register categories, from its recipe.
- **Sold.** A dish sold leaves the stock as its ingredients: Odoo (`pos_mrp`) explodes the kit,
  at each order or at the session's closing, as the register is set.
- **Refunded.** Odoo puts a refunded dish's ingredients back in stock. The plate came back to
  the kitchen, not to the shelf: once that return transfer is done, its ingredients leave again
  as a loss, reason Customer return, once. A return the register could not validate makes no
  loss until it is validated. An item sold as it is, not from a kitchen recipe (a bottle),
  goes back to the stock.
- In the kitchen's variance report, the register's sales have their own column ("Register");
  a count warns of register orders still open.

## What the module does not do

A loss that fails never blocks the return: it is written on the transfer, to declare by hand.

## Requirements

Odoo 18 with Point of Sale and its kits (`pos_mrp`), and `bf_kitchen`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
