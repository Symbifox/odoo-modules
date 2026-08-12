# Lexend Typeface

Adds the Lexend typeface as a font option across the Odoo backend, frontend
and PDF reports.

## What it adds

### Fields on `res.company`

- `font` — extends the standard Odoo selection with `Lexend`

Brand color fields and their Settings UI live in `bluefox_branding`
(since 18.0.3.0.0) — install that module to manage brand colours and
white-label settings.

### Asset bundles

- `web.assets_backend` + `web.assets_frontend` — `lexend.css` adds the
  Lexend `@font-face` declaration and a body-level font-family override
  when `res.company.font = 'Lexend'`
- `web.report_assets_common` + `web.report_assets_pdf` — same for PDF reports

## Standalone use

This module is useful on its own if you simply want the Lexend font
available as a `res.company.font` option for the backend, frontend and
PDF reports. The companion `bluefox_branding` module builds on it to
re-skin the backend UI and rewrite standard Odoo email templates.

## Dependencies

- `web` (Odoo core)
