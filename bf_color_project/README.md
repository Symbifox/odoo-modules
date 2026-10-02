# Symbifox Color for Projects (`bf_color_project`)

Bridge between [`bf_color`](../bf_color) and Odoo Project. Installs by itself when
both are present.

## What it does

- **Project tags** take a free color (`#RRGGBB`), and each person can keep their
  own color for a tag ("My color").
- **Tasks** can be colored by their tags with an automatic rule. With several
  tags on one task, the order of the rule's lines decides; "Listed values only"
  colors just the tasks carrying the tags you list (for instance *Business
  development*) and leaves the others alone. The task card's edge shows the color.
- **Projects** take a free color too, shown on their card and followed by every
  agenda that sorts by project (meeting agendas and records).

The project sharing views of the portal are left untouched.

## Requirements

Odoo 18 (`project`), `bf_color`.

## Licence

LGPL-3. See `LICENSE`.
