# Dashboard Split View (`bf_dashboard_split`)

A client-side Odoo view that shows **three Odoo actions at once** — top-left,
top-right and bottom — behind resizable splitters.

## Why

Odoo shows one action at a time. Some work is comparison work: a list of tickets
beside the project they belong to, a pipeline beside the calendar it feeds. Doing
that with browser tabs loses the shared context, and building a bespoke dashboard
for every pairing does not scale.

This view takes three ordinary Odoo actions and frames them side by side. No new
models, no data of its own, no recompute — it composes what already exists.

## What it provides

- A `dashboard_split` view type rendering three panes in iframes.
- Draggable separators, both horizontal and vertical; the layout is per user.
- Each pane hosts any action the user is allowed to open, and keeps its own
  breadcrumb and filters.

## Requirements

Odoo 18 Community, `web`. Nothing else.

## License

LGPL-3.
