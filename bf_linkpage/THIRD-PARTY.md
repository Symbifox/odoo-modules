# Third-party works bundled with `bf_linkpage`

This module redistributes a typeface that is **not** owned by Les services de
consultation Blue Fox, Inc. It is bundled under its own licence, reproduced in
full alongside the files themselves. The BUSL-1.1 licence that governs
`bf_linkpage` itself does **not** apply to it, and nothing in that licence grants
or restricts any right in it.

## Typeface (`static/fonts/`)

Lexend is self-hosted rather than fetched from a font CDN, so no visitor's IP
address ever reaches a third party when a link page is opened. It is used only
by pages whose **Font** setting is "Lexend", and it is declared under a family
name of the module's own ("BF Linkpage Lexend"), so it never competes with a
"Lexend" the website may define.

| Typeface | Files | Licence |
|---|---|---|
| Lexend | `Lexend-Regular.woff2`, `Lexend-SemiBold.woff2` | SIL Open Font License 1.1 — [`Lexend.OFL.txt`](static/fonts/Lexend.OFL.txt) |

Copyright notice, as stated by the upstream project:

- Copyright 2018 The Lexend Project Authors (https://github.com/googlefonts/lexend), with Reserved Font Name "RevReading Lexend".

### Notes for redistributors

- The SIL Open Font License 1.1 permits bundling and redistribution, including
  with commercial software, provided the licence and copyright notice travel
  with the font files. Both are present in `static/fonts/`.
- The font files are WOFF2 conversions of the upstream regular and semibold
  TrueType files.
