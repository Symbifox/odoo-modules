# Symbifox: kitchen and school meals (`bf_kitchen_school_meal`)

Bridge between the kitchen (`bf_kitchen`) and the school meals (`bf_school_meal`), for a
caterer that cooks in the same Symbifox as the school. Installed on its own when both are there.

## Features

- **A meal is a dish.** A meal of the school menu points to its dish in the kitchen. The kitchen
  manager reads its plate cost and its food cost ratio against the meal's price.
- **Allergens.** The dish's allergens, carried up from its ingredients, count with the meal's
  own: a meal is refused at the order (office or family portal) to a student allergic to either,
  and the portal shows both.
- **Served meals leave the stock.** Once a family can no longer cancel (the school's hour, in
  its time zone), the meals still ordered for the day leave the stock as their ingredients, in
  one Kitchen consumption transfer per school and day, from the company's kitchen warehouse. A day
  that cannot leave (no stock for an item tracked by lot, no warehouse) is said once on the school
  and on the day; the hourly pass retries until it works. Each meal leaves once; a meal added later
  leaves at the next hourly pass. The meals of days before the bridge was installed are left
  alone.
- **Cancelled after the hour.** A meal cancelled, or a day closed, after that hour puts nothing
  back in stock (the food is cooked): in the kitchen's variance report, it moves from
  consumption to losses ("Meal cancelled after the cut-off"), by the dish's current recipe.

## What the module does not do

The caterer is the school's own company, in the same database. A caterer that is another
business, on its own Symbifox, is not covered. A meal with no dish takes nothing out.

## Requirements

Odoo 18, `bf_kitchen` and `bf_school_meal`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
