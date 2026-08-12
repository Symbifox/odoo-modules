# Fundraising — Web & donor portal (`bf_fundraising_web`)

The online service layer for the fundraising suite.

## Public donation form (`/don`)

A visitor enters their name, email, address, amount and, optionally, the fund
and campaign. The donation is created in Odoo, and the donor record is
**matched by email or created** (flagged as a constituent). The donation starts
as a **draft**; when staff **validate** it, the **official receipt** is issued
(through `bf_receipt_ca`) and **emailed** automatically.

The form lives at `/don`. No menu is added to the website automatically: each
organisation adds its own "Donate" link or button wherever it wants one (a
consulting site, for instance, should not show a donation link).

> Required configuration: a **donation product** must be set on the company
> ("Product for bank transfer donations") for the form to create the lines.

## Donor portal

A signed-in donor sees, under **My account**:

- **My donations** — their giving history (number, date, campaign, amount, link
  to the receipt);
- **My official receipts** — download of the CRA + RQ compliant PDF receipts.

Access is restricted to the donor (ownership is verified in the controllers,
rendering runs in `sudo`).

## Dependencies

`bf_receipt_ca`, `website`, `portal`. AGPL-3 licence.
