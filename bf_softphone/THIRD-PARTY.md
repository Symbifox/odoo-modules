# Third-party works bundled with `bf_softphone`

This module redistributes a JavaScript library that is **not** owned by Les
services de consultation Blue Fox, Inc. It is bundled under its own licence,
reproduced in full alongside the file itself. The LGPL-3 licence that governs
`bf_softphone` does **not** apply to it, and nothing in that licence grants or
restricts any right in it.

## JavaScript library (`static/src/lib/`)

JsSIP is the SIP-over-WebSocket stack the browser softphone runs on. It is
self-hosted rather than loaded from a CDN, so no visitor IP reaches a third
party and the version cannot change under the module's feet.

| Library | File | Version | Licence |
|---|---|---|---|
| JsSIP | `jssip.min.js` | 3.11.1 | The MIT License — [`JsSIP.LICENSE.md`](static/src/lib/JsSIP.LICENSE.md) |

Copyright notice, as stated by the upstream project:

- Copyright (c) 2012-2015 José Luis Millán - Versatica
  (<https://github.com/versatica/>), with José Luis Millán
  <jmillan@aliax.net> as author and Iñaki Baz Castillo <ibc@aliax.net> as core
  developer. The bundled build's own banner carries a later copyright range.

### Notes for redistributors

- The file is shipped exactly as it is used in production, unchanged in this
  repository. Its SHA-256 is
  `38008af40965396308c82652ac3c03fd54ae12fdb191089f135ebb2f53e5b2be`
  (239 810 bytes) — pin it if you mirror this module, and re-check it after any
  upgrade.
- The library is **not** declared in the manifest's `assets`. It is ~240 KB and
  is loaded lazily with `loadJS`, only for a member of the phone group who
  opens the panel, so it never enters the back-end bundle served to everyone.
- The MIT License permits redistribution, including inside a work under a
  different licence, provided the copyright notice and the permission notice
  travel with it — which is what this file and the licence beside the library
  are for.
- Upstream: <https://jssip.net>.

## A note on the module's own dependency

Not a bundled work, but it bears on what you may do with this one:
`bf_softphone` depends on `bf_sms_archive`, published in this same repository
under the **Business Source License 1.1**, not LGPL-3. This module does not
load without it. The LGPL-3 grant on the code here therefore does not, on its
own, let you provide the pair as a service to third parties — read
`bf_sms_archive/LICENSE`, which carries its Additional Use Grant and its
Change Date.
