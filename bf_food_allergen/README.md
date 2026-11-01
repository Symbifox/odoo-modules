# Symbifox: food allergens (`bf_food_allergen`)

One list of Health Canada's priority food allergens (peanuts, tree nuts, sesame, milk, eggs,
fish, crustaceans and molluscs, soy, wheat and gluten, sulphites, mustard), shared by every
Symbifox food module: the kitchen's recipes (`bf_kitchen`) and, later, the school
caterer's meals (`bf_school_meal`). The list is data: a kitchen can add its own entries or
archive one. An allergen still declared (by a kitchen item, a student or a school meal) is
neither archived nor deleted.

## Requirements

Odoo 18 (`product`). The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first version.
- 18.0.1.1.0: an allergen still declared (by a kitchen item, a student or a school meal) cannot be
  archived or deleted; each module that declares allergens tells what uses one.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
