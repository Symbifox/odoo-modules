# Partner Legal Name (`partner_legal_name`)

A company's registered legal name, kept apart from the name used every day.

Many organisations trade under a name that is not their registered one: "Acme"
on the website, "Acme Holdings Inc." on contracts and invoices. Odoo has a single
name field. This module adds the legal name next to it.

## What it does

- **Legal name** (`legal_name`) on contacts, next to the tax ID. Changes are
  tracked in the chatter when `mail` is installed. Left empty, documents fall
  back to the contact name.
- **Legal name** on companies, the same value as on the company's contact.
- **Document header**: in the *Folder* layout of external documents (quotes,
  invoices, reports), the legal name is printed above the company address when
  it differs from the company name.
- **Document layout wizard**: the field is exposed on
  *Settings > General > Configure Document Layout*, so the preview renders the
  header without error.
- `legal_or_display_name` on contacts, for reports and templates: the legal name
  when set, the name otherwise.

The field is meant for companies. It belongs to no group, so every internal
user can read it: a person's legal name does not belong here. With
`bf_preferred_name`, the bridge `bf_preferred_name_partner_legal_name` hides it
on individuals and keeps a person's legal name in a protected field.

## Requirements

- Odoo 18.0: `base`, `web`.

## Languages

English source, French (`fr_CA`) translation included.

## Changelog

- **18.0.1.0.2**: First public release. French translation, tests. Earlier
  versions ran in production only.

## Licence

LGPL-3. See [LICENSE](LICENSE).
