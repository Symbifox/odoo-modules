# Symbifox: kitchen (`bf_kitchen`)

Recipes and plate cost for restaurants, caterers and kitchens, built on Odoo's stock,
purchase and kits: recipes and plate cost, then the kitchen stock (counts, losses,
preparations made ahead). Sales from every source with the theoretical/actual variance,
supplier orders and menu engineering are planned for later releases.

## Features

- **Recipes on kits.** A dish is a bill of materials of type kit: when it leaves the stock,
  at the register or at delivery, Odoo explodes it into its ingredients. Nothing is
  rewritten.
- **Net, yield, gross.** Each recipe line holds the net quantity on the plate and the yield
  (45 % for a whole salmon). The gross quantity, the one that leaves the stock, follows. The
  yield is copied from the ingredient when the line is created, then kept.
- **Sub-recipes.** A stock, a sauce, a dough is a preparation used in other recipes; its cost
  follows its own ingredients. Saving a recipe refreshes its cost and the cost of every
  recipe that uses it; a preparation or a dish without an active recipe costs 0.
- **Ingredient cost from the last bill.** The cost of one stock unit comes from the last
  validated vendor bill by bill date (a late-entered older bill does not replace a newer
  price), net of discount and taxes, in the company currency, brought back to the stock
  unit. A bill put back to draft, cancelled or fully reversed by posted credit notes
  (reconciled or not) falls back to the previous one; a partial credit note keeps its price. Costs are kept per company and per variant. An entered cost serves until
  the first bill (typing the kitchen cost of an ingredient with no bill enters it). A nightly
  job recomputes every ingredient from its bills and every preparation and dish from its
  recipe.
- **Never blocks a validation.** A bill line whose unit cannot be brought back to the stock
  unit goes to **Costs to check**; the computation runs in a savepoint.
- **Plate cost, food cost, suggested price.** Per recipe: plate cost, food cost ratio, target
  (30 % by default), suggested price with the company's rounding (0.05, 0.25, dollar, .95),
  margin, and a flag when an ingredient cost is older than 60 days.
- **Alert.** When a bill pushes a dish over its target (or further over it), the kitchen
  manager gets one activity, with the ingredients and the bill. The activity carries no
  figure (anyone who reads the product reads it): the figures are on the recipe.
- **Lines to attach.** Bill lines with no product, from suppliers marked "food supplier",
  are attached once to an ingredient with their format (a case of 12 x 1 L). The posted
  line itself is not rewritten. The vendor line created on the ingredient (code, name,
  format and its billed unit, price per purchase unit in the bill currency) makes the next
  bill, typed or read by OCR, carry the product. The format applies only to the lines in its
  billed unit: a line of the same vendor in g is read in g.
- **Received.** A button on a draft vendor bill without a purchase order brings the
  ingredients into stock on the day they arrive, once per line, valued at the bill line's
  cost to the fraction of a cent (in perpetual valuation, the stock interim account is
  settled by the bill). Only lines with a product: a line without one is booked to an
  expense account; on a draft bill, set the ingredient as its product first. Cancelling the
  bill sends back what is still in, up to what is on hand in the receipt's location (a
  return already made from Inventory is deducted; what was already used stays, is noted on
  the bill, and keeps the line counted as received). A line sent back in full can be
  received again. A received line keeps its product, unit and quantity, and a received bill
  or line cannot be deleted while the goods are in.
- **Allergens.** Declared on the ingredient (Health Canada's priority list, from
  `bf_food_allergen`), carried up to preparations and dishes, plus "may contain".
- **Printouts.** Technical sheet with or without costs; allergen sheet of the menu.
- **Kitchen locations.** Walk-in cooler, freezer, dry storage and bar under the stock of the
  warehouse, made for the companies that cook (or from the settings), in the order a cook
  walks through them. Each item can say where it is stored.
- **Counts.** A count walks through every location of the warehouse, the stock location
  last: sales, receipts, losses and preparations move the stock there, and it can be
  negative. Each location is applied as soon as it is entered (a count applied hours later
  would put back what was sold in between). Whole purchase units plus what is left of an
  opened one; "None here" counts 0; an item not entered keeps its stock. The variance is
  kept, valued at kitchen cost. Before counting, the screen warns of transfers not validated
  and register orders still open. A weekly, biweekly or monthly reminder prepares the next
  count for the kitchen manager. Items tracked by lot are left to the Inventory app.
- **Losses.** What, how much, why, in a few taps on a phone: a scrap is recorded and
  validated at once, even when Odoo's figures say there is not that much (a loss is a fact).
  A dish lost leaves the stock as its ingredients. Six reasons are shipped (expired, cooking
  failure, customer return, spilled or dropped, staff meal, tasting). Each loss carries its
  value at kitchen cost, for the kitchen manager.
- **Preparations made ahead.** A preparation can be held in stock (a stock, a sauce): the
  dishes then take it from the stock, and "I prepared" records a batch, its ingredients out
  and the preparation in, in one tap. At standard cost, Odoo's default, a batch enters at the
  preparation's inventory cost (0 until someone sets it) while its ingredients leave at their
  value: put the preparations held in stock in a product category at average cost (AVCO) or
  FIFO, where a batch enters at the value of its ingredients. Making it on the
  fly again is refused while some of it is in stock or a batch is in progress, and either
  switch waits for the deliveries and register orders already under way.

## Units

Stock each ingredient in its smallest kitchen unit (g, ml, unit). Odoo rounds a kit line up
to its unit, then converts to the stock unit: 3 g of salt stocked in kg becomes 0.00 kg.
What is refused: in a preparation, a line whose quantity becomes 0 in the stock unit; in a
dish, an ingredient whose quantity for one portion, sub-recipes exploded as Odoo does at the
sale, becomes 0. A dish is checked again when its number of portions or unit changes, and
when a preparation it uses changes.
A purchase unit such as "bunch (60 g)" can be created in the Weight category.

## Rights

- **Cook**: reads recipes without their costs, counts the stock, declares losses and records
  the batches of preparations made ahead. Implies Inventory / User.
- **Kitchen manager**: writes recipes and kitchen products through the module's own access
  rights, without any Manufacturing group. That write is limited to kitchen products and
  recipes; a user who can already write products through another group (stock, purchase,
  sales, accounting, manufacturing manager) keeps that access whole. Within that limit, the
  vendor lines of kitchen products are writable too, and the fields that settle accounting
  and valuation are not: taxes, accounts and other properties, cost, category, company, type.
- Lines to attach and costs to check need Accounting / Billing as well, and so do attaching
  and ignoring a line. Only a kitchen ingredient can be attached. The kitchen fields of a
  bill line (ingredient, format, receipt, cost issue), the link between a receipt and its bill
  line, the kitchen type of a recipe and the computed costs are written by the module's own
  buttons and engine only, never directly. The technical sheet with costs is refused to anyone but the kitchen
  manager, also at its report address.
- Odoo itself lets every internal user read the product cost (`standard_price`) and the
  vendor prices (`product.supplierinfo.price`). The module hides its own costs from the
  cook, not those native fields.

## What the module does not do yet

The stock valuation stays Odoo's: the module never writes the product cost. Sales sources,
the theoretical/actual variance, supplier orders and menu engineering are planned for later
releases. The kitchen locations list is the Inventory administrator's (writing a location is
theirs in Odoo).

## Requirements

Odoo 18 with Inventory with stock valuation (`stock_account`), Purchase (`purchase_stock`)
and Manufacturing (`mrp`, for the kits), and `bf_food_allergen` for the list of
allergens. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release, recipes and plate cost.
- 18.0.1.0.1: the recipe title takes the full width of the form (a long dish name was cut).
- 18.0.1.0.2: the Attach wizard has its title. The printed sheets
  carry the company of the recipe and print their numbers in the reader's language (120, 2,5,
  33,4 %), with the allergen marks centred under their heading. The preparation list and form
  no longer show the columns of a dish. The recipe lines have short headings that fit. The
  ingredient cost reads per kg or per L with its currency (27.50 $ / kg), and the entered cost
  is hidden once a bill gives the cost. A vendor format is above 0, or empty. The plate cost
  activity is written in the language of the manager who receives it. New app icon (a chef's
  toque).
- 18.0.1.0.3: a preparation's cost per unit made reads per kg or per L (0.84 $ / L, not 0.00 $), and
  the preparation list headings fit. The ingredient and preparation forms show their readable
  cost in one piece (27.50 $ / kg).
- 18.0.1.1.0: the kitchen stock. Kitchen locations, counts walked location by location and
  applied as they are entered, losses in a few taps (a dish lost leaves as its ingredients),
  preparations held in stock with "I prepared", a count reminder.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
