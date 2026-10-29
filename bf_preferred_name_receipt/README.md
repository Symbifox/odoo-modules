# Preferred Names and Pronouns: Donation Receipts Bridge (`bf_preferred_name_receipt`)

The official donation receipt carries the donor's legal name.

An official donation receipt for income tax purposes is an act. This bridge
prints on it the donor's **legal name** (`bf_preferred_name`), or their
preferred name when they have no legal name on file. The contact and the emails
keep the preferred name.

## What it does

- In the donation tax receipt of `bf_receipt_ca`, the donor name is the legal
  name.
- The donor address block prints the address only. Odoo's address widget keeps
  the contact name in a block that only the stylesheet hides; when the
  stylesheet does not load in the PDF engine, the preferred name would print
  under the legal name.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_receipt_ca` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
