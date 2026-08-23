# Helpdesk fields for the website form builder (`bf_helpdesk_website_form`)

Opts the fields of `helpdesk.ticket` into Odoo's website form builder, so a
public ticket form can be laid out from the page editor instead of from XML.

## Why

`helpdesk_mgmt` gives you a ticket model; the website form builder gives you a
drag-and-drop form. They do not meet by default: the builder only offers fields a
model has explicitly allowed. This module makes that declaration, and nothing
else.

## What it provides

- `helpdesk.ticket` is registered as a target model for the form builder.
- Its user-writable fields become available in the builder's field list.
- No controller, no route, no override of the ticket's own create logic — a
  submission goes through Odoo's standard website-form pipeline.

## Requirements

Odoo 18 Community, `website`, and OCA `helpdesk_mgmt`.

## Caution

Exposing a field in a public form means an anonymous visitor can set it. Review
which fields you enable, and keep the ones that drive assignment, priority or
billing out of the form.

## License

LGPL-3.
