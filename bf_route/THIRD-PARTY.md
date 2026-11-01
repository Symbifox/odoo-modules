# Third-party works bundled with `bf_route`

This module redistributes a JavaScript library that is **not** owned by Les
services de consultation Blue Fox, Inc. It is bundled under its own licence,
reproduced in full alongside the files themselves. The Business Source License
1.1 that governs `bf_route` does **not** apply to it, and nothing in that
licence grants or restricts any right in it.

## JavaScript library (`static/lib/leaflet/`)

Leaflet draws the map of a route day, on the phone and on the day's form. It is
self-hosted rather than loaded from a CDN, so no visitor IP reaches a third
party for the library itself and the version cannot change under the module's
feet. (The map's background tiles are another matter: see *Known limits* in the
README.)

| Library | Files | Version | Licence |
|---|---|---|---|
| Leaflet | `leaflet.js`, `leaflet.css` | 1.9.4 | BSD 2-Clause License — [`LICENSE`](static/lib/leaflet/LICENSE) |

Copyright notice, as stated by the upstream project:

- Copyright (c) 2010-2023, Volodymyr Agafonkin
- Copyright (c) 2010-2011, CloudMade

### Notes for redistributors

- The files are the upstream 1.9.4 release build, unchanged. Their SHA-256:
  - `leaflet.js` (147 552 bytes):
    `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a`
  - `leaflet.css` (14 806 bytes):
    `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6`

  Pin them if you mirror this module, and re-check them after any upgrade.
- The library is **not** declared in the manifest's `assets`. It is loaded
  lazily with `loadJS` / `loadCSS` the first time a map opens, so it never
  enters the back-end bundle served to everyone.
- The BSD 2-Clause License permits redistribution, including inside a work under
  a different licence, provided the copyright notice, the list of conditions and
  the disclaimer travel with it — which is what the licence file beside the
  library is for.
- Upstream: <https://leafletjs.com>.
