# Symbifox Color for Helpdesk (`bf_color_helpdesk`)

Bridge between [`bf_color`](../bf_color) and the helpdesk (`helpdesk_mgmt`). Installs by itself
when both are present.

## What it does

Helpdesk tags and teams take a free color instead of Odoo's twelve, and each
person can keep their own color for a tag. Tickets carry a free color too,
which an automatic rule can set (from their tags, as for project tasks), and
ticket cards are painted with it.

## Requirements

Odoo 18, OCA `helpdesk_mgmt` (AGPL-3), `bf_color`.

## Licence

AGPL-3. See `LICENSE`. The bridge extends models and views of the OCA
`helpdesk_mgmt` module, which is AGPL-3, so it inherits that licence.
