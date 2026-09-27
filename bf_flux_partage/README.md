# RSS Feeds: external sharing (`bf_flux_partage`)

A project's news watch often matters to the client as much as to the team, and
a client has no account. This module gives a list of RSS items **secret share
links**, usable without logging in.

- **A public page** in the company's colours, showing the latest 50 items the
  list retained as cards: image, source, date, title, summary, link.
- **The same content as RSS** (the link followed by `/rss`), to subscribe in
  any reader.
- **One link per recipient**, with an expiry date (90 days by default), a visit
  count and the last access. Unticking "Active" revokes a link at once;
  regenerating it issues a new token and kills the old URL.

## What never leaves

- The full text of articles: republishing third-party content on a public page
  is not ours to do. Title, summary and link, like any aggregator.
- Anything internal: the list's description, why an item was retained, AI
  scores and reasons, items set aside, who read what, and the address of any
  feed (a feed URL may carry a private token). Only the names of the list's own
  sources are shown.
- Links that are not `http(s)`.

The page and the feed are sent with `noindex`, `no-referrer` and `no-store`:
not indexed, the secret address is not leaked to the sites a reader visits, and
a revoked link stops answering even in a browser that opened it before. An
unknown, expired or revoked token answers the same 404.

Managing links is reserved to the RSS managers group.
