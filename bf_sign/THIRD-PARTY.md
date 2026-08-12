# Third-party works bundled with `bf_sign`

This module redistributes works that are **not** owned by Blue Fox Inc. They are
bundled under their own licences, which are reproduced in full alongside the
files themselves. The licence that governs `bf_sign` itself does **not** apply to
them, and nothing in that licence grants or restricts any right in them.

## Typefaces (`static/fonts/`)

Three Google Fonts are self-hosted so that a typed signature or set of initials
renders identically on every device, without any request to a third-party font
service. They are offered in the "type your signature" styles on the public
signing page.

| Typeface | Style label in the UI | Files | Licence |
|---|---|---|---|
| Caveat | « Manuscrit » | `Caveat.woff2` | SIL Open Font License 1.1 — [`Caveat.OFL.txt`](static/fonts/Caveat.OFL.txt) |
| Dancing Script | « Cursif » | `DancingScript.woff2` | SIL Open Font License 1.1 — [`DancingScript.OFL.txt`](static/fonts/DancingScript.OFL.txt) |
| Great Vibes | « Élégant » | `GreatVibes.woff2` | SIL Open Font License 1.1 — [`GreatVibes.OFL.txt`](static/fonts/GreatVibes.OFL.txt) |

Copyright notices, as stated by the upstream projects:

- Copyright 2014 The Caveat Project Authors (https://github.com/googlefonts/caveat)
- Copyright 2016 The Dancing Script Project Authors (https://github.com/googlefonts/DancingScript), **with Reserved Font Name 'Dancing Script'**
- Copyright 2015 The Great Vibes Pro Project Authors (https://github.com/googlefonts/great-vibes)

### Notes for redistributors

- The SIL Open Font License 1.1 permits bundling and redistribution, including
  with commercial software, provided the licence and copyright notice travel
  with the font files. Both are present in `static/fonts/`.
- **Dancing Script carries a Reserved Font Name.** Under OFL clause 3, a
  modified version of that font may not be distributed under the name
  "Dancing Script". Blue Fox ships it unmodified. If you modify it, rename it.
- The fonts must not be sold on their own, per OFL clause 2. Bundling them
  inside this module is not a sale of the fonts.

## Python dependencies

`bf_sign` imports the following libraries at runtime. They are **not**
redistributed with this module; they are installed separately and remain under
their own licences: `asn1crypto`, `cryptography`, `Pillow` (`PIL`), `pyhanko`,
`pyhanko_certvalidator`, `PyPDF2`, `reportlab` and `requests`.

## PDF.js

The signing page renders the document with the copy of PDF.js that Odoo already
serves. No copy of PDF.js is vendored into this module.
