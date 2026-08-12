# Donation receipts — Canada (`bf_receipt_ca`)

Makes the **Donations** module's receipts compliant with **CRA** and **Revenu
Québec** requirements, **in French**. This is the key differentiator against
Raiser's Edge, whose receipts follow US rules.

## What the official receipt contains

Every element the CRA requires: the "Official receipt for income tax purposes"
statement, the organisation's legal name and address, the **registration number
(BN/RR)**, a **unique serial number** (`REÇU-YYYY-NNNNN`, reset each year, with
no gaps), the issue date, the donation date/year, the donor's name and address,
the **donation amount**, the **advantage amount**, the **eligible amount**, an
authorised signature, and the CRA name plus the address
`canada.ca/organismesdebienfaisance` (the receipt is issued in French, so it
carries the French CRA URL).

**Gifts in kind**: description of the property, **fair market value**,
appraiser.

## Specifics

- A single French receipt carrying the BN/RR number satisfies both the federal
  and the Quebec requirements (automatic recognition in Quebec since 2016).
- "Place of issue" and "appraiser" fields are **configurable** (2024 CRA
  modernisation).
- **Cancellation / reissue** with a replacement chain; cancelled receipts are
  retained (a CRA requirement).

## Configuration

On the company (Settings → Companies): registration number (BN/RR), authorised
signatory, signature image, display options.

The PDF receipt reuses the company's branded document layout
(`web.external_layout`) and the **Lexend** typeface (`bf_lexend`).

## Branded receipt email

The email carrying the receipt is **branded the same way as the other Symbifox
modules**: when installed, `bluefox_branding` provides the `bf_mail_layout`
transactional layout (header with logo, company colours and footer), resolved at
runtime through `donation.tax.receipt.bf_receipt_email_layout()`. Without
`bluefox_branding`, the email falls back to Odoo's standard light layout, which
makes it an **optional dependency** (not declared in `depends`).

## Dependencies

Required: `bf_fundraising_core`, `bf_lexend`.
Optional: `bluefox_branding` (branding of the PDF and the email). AGPL-3 licence.
