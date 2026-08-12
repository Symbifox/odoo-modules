# Third-party works bundled with `bf_lexend`

This module redistributes a typeface that is **not** owned by Les services de
consultation Blue Fox, Inc. It is bundled under its own licence, reproduced in
full alongside the files themselves. The LGPL-3 licence that governs
`bf_lexend` itself does **not** apply to it, and nothing in that licence grants
or restricts any right in it.

## Typeface (`static/fonts/`)

Lexend is self-hosted so the Odoo backend, frontend and PDF reports render
identically on every device, with no request to a third-party font service.

| Typeface | Files | Licence |
|---|---|---|
| Lexend | `Lexend-Regular.ttf`, `Lexend-SemiBold.ttf` | SIL Open Font License 1.1 — [`Lexend.OFL.txt`](static/fonts/Lexend.OFL.txt) |

Copyright notice, as stated by the upstream project:

- Copyright 2018 The Lexend Project Authors (https://github.com/googlefonts/lexend), **with Reserved Font Name “RevReading Lexend”**

### Notes for redistributors

- The SIL Open Font License 1.1 permits bundling and redistribution, including
  with commercial software, provided the licence and copyright notice travel
  with the font files. Both are present in `static/fonts/`.
- The Reserved Font Name is **“RevReading Lexend”**, not “Lexend”. Files named
  `Lexend-*` are therefore fine to redistribute under that name. If you produce
  a modified version, do not call it “RevReading Lexend”.
- These are the upstream static instances (version 1.008), shipped unmodified.
  TrueType rather than WOFF2 because wkhtmltopdf renders TTF reliably and WOFF2
  inconsistently, and the same files serve both the web bundles and PDF reports.
- The fonts must not be sold on their own, per OFL clause 2. Bundling them
  inside this module is not a sale of the fonts.
