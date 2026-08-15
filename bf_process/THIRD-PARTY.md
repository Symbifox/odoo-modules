# Third-party works bundled with `bf_process`

This module redistributes a typeface that is **not** owned by Les services de
consultation Blue Fox, Inc. It is bundled under its own licence, reproduced in
full alongside the files themselves. The BUSL-1.1 licence that governs
`bf_process` itself does **not** apply to it, and nothing in that licence grants
or restricts any right in it.

## Typeface (`static/fonts/`)

Lexend is self-hosted here for a stricter reason than elsewhere in this
repository. `generateur/lexend_metriques.py` is a frozen table of this
typeface's glyph advances, and the module computes annotation heights and
external-pool header heights from that table rather than from a font engine.
The bundled files are therefore not a styling preference: they are the
reference the measurements were calibrated against. Substituting a different
font, or a different version of this one, would silently change every computed
height and make the on-screen trace, the two XML exports and the PDF disagree.

| Typeface | Files | Licence |
|---|---|---|
| Lexend | `Lexend-Regular.ttf`, `Lexend-SemiBold.ttf` | SIL Open Font License 1.1 — [`OFL.txt`](static/fonts/OFL.txt) |

Copyright notice, as stated by the upstream project:

- Copyright 2019 The Lexend Project Authors (https://github.com/googlefonts/lexend)

### Notes for redistributors

- The SIL Open Font License 1.1 permits bundling and redistribution, including
  with commercial software, provided the licence and copyright notice travel
  with the font files. Both are present in `static/fonts/`.
- These are the upstream static instances (version 1.008, `ttfautohint
  v1.8.4.7`), shipped unmodified. The name table of these files declares no
  Reserved Font Name, so redistribution under the name "Lexend" is fine.
- TrueType rather than WOFF2 because `reportlab` embeds TTF directly when
  drawing the PDF, and the same files back the calibrated width table.
- The fonts must not be sold on their own, per OFL clause 2. Bundling them
  inside this module is not a sale of the fonts.
- If you regenerate `generateur/lexend_metriques.py` with
  `tools/etalonner_lexend.py`, run it against **these** files, and re-run the
  calibration check afterwards.
