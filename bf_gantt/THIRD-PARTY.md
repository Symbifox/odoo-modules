# Third-party works bundled with `bf_gantt`

This module redistributes a typeface that is **not** owned by Les services de
consultation Blue Fox, Inc. It is bundled under its own licence, reproduced in
full alongside the files themselves. The BUSL-1.1 licence that governs
`bf_gantt` itself does **not** apply to it, and nothing in that licence grants
or restricts any right in it.

## Typeface (`static/fonts/`)

Lexend is self-hosted rather than fetched from a font CDN, so no reader's IP
address ever reaches a third party when a schedule is drawn or printed. The
module needs the files locally in any case: `generateur/pdf.py` registers the
TrueType files with `reportlab` and measures label widths against them, and
`generateur/png.py` loads the same files through Pillow. A schedule drawn with a
different typeface would truncate its labels at different points.

| Typeface | Files | Licence |
|---|---|---|
| Lexend | `Lexend-Regular.ttf`, `Lexend-SemiBold.ttf` | SIL Open Font License 1.1 — [`OFL.txt`](static/fonts/OFL.txt) |

Copyright notice, as stated by the upstream project:

- Copyright 2019 The Lexend Project Authors (https://github.com/googlefonts/lexend)

### Notes for redistributors

- The SIL Open Font License 1.1 permits bundling and redistribution, including
  with commercial software, provided the licence and copyright notice travel
  with the font files. Both are present in `static/fonts/`.
- These are the upstream static instances, shipped unmodified. The name table of
  these files declares no Reserved Font Name, so redistribution under the name
  "Lexend" is fine.
- TrueType rather than WOFF2 because `reportlab` embeds TTF directly when drawing
  the PDF, and Pillow reads the same files for the PNG.
- The fonts must not be sold on their own, per OFL clause 2. Bundling them inside
  this module is not a sale of the fonts.
- If the files are missing or unreadable, the PDF falls back to Helvetica and the
  PNG to Pillow's default face rather than failing — the document still comes
  out, only its label truncation shifts.
