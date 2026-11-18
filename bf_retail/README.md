# Retail prices (`bf_retail`)

The cost of what a store buys, kept as a series, and the shelf price that follows it.

A corner store, a dairy, a small grocery buys to resell: its cost moves every week and its
margin is made of cents. Odoo keeps one cost per product. One number cannot say whether an
item went up twice this month, and that is what has to be seen before a shelf price moves.

## What it adds

- **Retail categories.** A category followed at retail, its target margin and its price
  rounding, inherited by the categories below it.
- **A cost series** per product and per company: one row per cost observed, with its date,
  where it comes from, the supplier, the bill line, and the change against the previous cost.
  A validated vendor bill or purchase receipt writes it by itself, net of discount and taxes,
  per unit of the product's own unit of measure. One bill gives **one row per product**: its
  lines are added up, rebates included, so the cost is what was really paid per unit. A bill
  that goes back to draft or is cancelled takes its rows out of the series, and a cost dated
  in the future becomes the current cost on its own day, not before.
- **The margin, always excluding tax.** A shelf price that includes GST and a price that
  excludes the sales taxes give the same margin on the same cost.
- **Price changes in batches**: proposed from the costs, applied in one walk down the aisle,
  and refused, by name, when the price or the cost moved since the proposal, when a product
  is shared with another store that follows the retail, or when a variant's price cannot be
  moved without moving its sister variants. A variant is priced by its own extra, so the
  sisters hold still. The proposal leaves out, and names, what the application would refuse,
  and it keeps the lines a manager decided by hand.
- **A shelf label queue**, printed with Odoo's own label layout and marked printed by the
  person who walked the aisle.
- **A journal of shelf price moves**: every move of a followed product's price, from a batch
  or typed on the product, with the day, who wrote it and the batch line when there is one.
- **The cost against price index**: per category and over a period, how much the cost moved,
  how much the shelf price moved, and the spread between the two. It reads the journal, so a
  rise passed by hand counts like a rise passed by a batch.

## What it does not do

The stock valuation cost is Odoo's: this module reads bills and keeps its own series, it
never writes `standard_price`. A credit note of its own (a return of bread, of milk, of what
did not sell) is not a cost for the next unit and writes nothing; a credit carried as a
negative line of the bill itself, on a product the same bill buys, lowers what was paid for
those units, because that is what the store paid. The shelf price is Odoo's
`list_price`, which is **not** kept per company: a product no store owns, while a second
store follows the retail, is refused and named rather than priced for both.
Space, facings and what each shelf earns belong to the planogram, which is another module.

## What it writes in the chatter

Every line this module leaves on a record is a **log**, not a notification: it stays on the
record and it sends no email, raises no bell. That is on purpose. A manager without an email
address on their user is an ordinary thing in a store, and a notification would have made the
price change itself fail for them — the message would have refused an author with no address,
and taken the business action down with it. The cost is named rather than hidden: in a store
with two managers, the one who proposed a batch is not told by email that the other applied it.

And the body comes out in the **language of the store**, not in the language of whoever set it
off. A batch applied at night by a scheduled action, or by a manager working in English inside
a French store, reads like every other line of the same chatter — because what the chatter
keeps is read later, by someone else, and the language of the reader is the store's.

## Groups

- *Shelf clerk*: reads the label queue and marks it printed. No cost, no margin.
- *Retail manager*: the costs, the targets and the prices. Reads and **writes** products,
  product templates and product categories, without creating and without deleting: making or
  removing an article stays with the buyer and the inventory clerk. No group of Odoo's own is
  implied, so the role carries no menu nobody asked for.

License: Business Source License 1.1 (see LICENSE).
