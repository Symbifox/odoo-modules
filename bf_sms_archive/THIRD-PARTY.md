# Third-party works bundled with `bf_sms_archive`

This module redistributes a typeface that is **not** owned by Les services de
consultation Blue Fox, Inc. It is bundled under its own licence, reproduced in
full alongside the file itself. The LGPL-3 licence that governs
`bf_sms_archive` does **not** apply to it.

## Typeface (`static/fonts/`)

Noto Emoji is embedded as a base64 data URI in the CSS of PDF exports, so an
SMS thread renders its emoji identically whatever fonts the rendering server
happens to have installed.

| Typeface | Files | Licence |
|---|---|---|
| Noto Emoji | `NotoEmoji-Regular.ttf` | SIL Open Font License 1.1 — [`NotoEmoji.OFL.txt`](static/fonts/NotoEmoji.OFL.txt) |

Copyright notice, as stated by the upstream project:

- Copyright 2013 Google LLC

### Notes for redistributors

- **This file is a modified version.** Upstream ships Noto Emoji as a variable
  font with a weight axis; what is bundled here is a static instance pinned at
  `wght=400`, produced with fontTools, so that the embedded data URI is 866 KB
  rather than 2 MB. The font tables are otherwise untouched and the upstream
  copyright notice is intact in the font's own name table.
- Noto Emoji declares **no Reserved Font Name**, so OFL clause 3 does not
  require the modified version to be renamed, and it keeps the name “Noto Emoji”.
- The font must not be sold on its own, per OFL clause 2. Bundling it inside
  this module is not a sale of the font.
