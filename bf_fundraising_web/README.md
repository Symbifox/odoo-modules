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

Submissions are **rate-limited per IP address**. The limiter keeps at most
10,000 addresses in memory: past that bound it drops expired addresses first,
then the lightest ones, and a blocked address is evicted last, so flooding the
limiter with new addresses never unblocks an address that hit its limit.

## Donor portal

A signed-in donor sees, under **My account**:

- **My donations** — their giving history (number, date, campaign, amount, link
  to the receipt);
- **My official receipts** — download of the CRA + RQ compliant PDF receipts.

Access is restricted to the donor (ownership is verified in the controllers,
rendering runs in `sudo`).

## Languages (18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The message refusing a donation when no donation product is configured, which the visitor of the public form can see, follows the language of the request.

## Dependencies

`bf_receipt_ca`, `website`, `portal`. AGPL-3 licence.

## Changelog

### 18.0.1.1.0

- The donation form, the donor portal and the module's labels are written in English in the source; the French ships in `i18n/fr_CA.po`. See the Languages section.

### 18.0.1.0.4

- The donation form's rate limiter no longer resets every address once it
  tracks more than 10,000 of them: it evicts expired, then lightly used
  addresses, and keeps a blocked address blocked. New regression test.
