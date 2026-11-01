# Preferred Names and Pronouns: Partner Legal Name Bridge (`bf_preferred_name_partner_legal_name`)

One legal name on a person's contact: the protected one.

`partner_legal_name` adds a **Legal name** field to contacts, meant for a
company's registered name. It belongs to no group, so every internal user reads
it, and its changes are tracked in the chatter. On a person's contact it sat
next to the protected **Personal legal name** of `bf_preferred_name`, and
invited people to type there a name that every internal user would read.

## What it does

Installed automatically when both modules are present, the bridge:

- **hides** that field on an individual's contact, where only the protected
  personal legal name remains;
- **renames** it **Company legal name** (*Nom légal de la société* in French)
  on a company's contact, so the two are not confused;
- **closes** it on a person's contact: a value is refused whatever the path
  (API, import), except on the contact of an Odoo company, whose legal name the
  document header reads. A company type set through import or the API
  (`company_type`) counts as the "company" box. To turn a company into an
  individual, clear its company legal name first;
- on installation, **moves** the values already typed on persons to the
  protected personal legal name, without a trace in the chatter, and erases the
  tracking of that field on those contacts. A protected legal name already set
  is never overwritten: if the two differ, the contact is left as it is and
  reported in the log, by its number only.

It does not change companies' legal names or the document header.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `partner_legal_name` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-11-01 for this version.
