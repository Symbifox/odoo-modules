# Lexend Typeface

Ships the Lexend typeface, self-hosted, for the Odoo backend, the website and
PDF reports. No request ever reaches a third-party font service.

## What it ships

Two static weights, regular (400) and semi-bold (600), in two formats:

| Where | Format | Size per weight |
|---|---|---|
| Backend and website | WOFF2, with the TTF as fallback in the same `@font-face` | about 37 KB |
| PDF reports | TTF | about 100 KB |

Every `@font-face` uses `font-display: swap`. Headings use the semi-bold weight.

## What changes once it is installed

- **Backend and website**: they render in Lexend right away, whatever the
  company font is. `lexend.css` sets the font on `html`, `body` and the web
  client; there is no setting to turn it off short of uninstalling the module.
- **PDF reports**: reports printed with the company document layout
  (invoices, quotations and the like) keep following the company font,
  `res.company.font` (Odoo's default is Lato). This module adds **Lexend** to
  that selection: pick it in the document layout settings to get Lexend in
  those reports too. Internal and plain reports, which have no company layout,
  render in Lexend.
- Tables in reports use tabular, lining figures so that amounts line up.

### Fields on `res.company`

- `font`: extends the standard Odoo selection with `Lexend`.

## What it does not do

Brand colours and white-label settings are not part of this module. Install
`bluefox_branding` for them; it builds on this module to re-skin the backend
and the standard Odoo emails.

## Dependencies

- `web` (Odoo core)

## Changelog

- **18.0.3.1.2**: description and README brought in line with what the module
  does. No code change.
- **18.0.3.1.1**: the backend and the website load Lexend as WOFF2 (about
  37 KB per weight instead of 100 KB), with the TTF as fallback. PDF reports
  keep the TTF.
- **18.0.3.1.0**: French name and summary in Apps (`fr_CA.po`).

## Third-party assets

The Lexend typeface is bundled under the SIL Open Font License 1.1, not the LGPL-3 that covers this module. See [`THIRD-PARTY.md`](THIRD-PARTY.md).
