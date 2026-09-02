# Editorial Workshop: Audience (`bf_editorial_audience`)

Odoo measures readership of an article in two ways that do not say the same
thing, and it says so nowhere.

- The post's native counter (`visits`) increments on every render of the page,
  with no user-agent check at all. A crawler has no session, so every crawler
  pass moves it. That is the true raw figure.
- `website.track` is already filtered: `_register_website_track` refuses to
  trace as soon as the agent contains one of Odoo's thirteen substrings (bot,
  crawl, slurp, spider, curl, wget, facebookexternalhit, whatsapp,
  trendsmapresolver, pinterest, instagram, google-pagerenderer, preview).

On a mid-sized blog the native counter commonly reads eight times the number of
article traces. The gap is what Odoo threw away.

## What this module adds

Odoo's filter is broad but coarse: it catches everything that *calls itself* a
robot, and lets through meta-externalagent, Barkrowler, DataForSeo,
python-requests, Go-http-client, okhttp, Scrapy, node-fetch and headless
browsers.

This module captures the user agent, derives a verdict and a family from it,
and records daily — per article and per language — four counters that add up:
the views Odoo kept, split into crawlers that got through, readers declaring a
browser, and agents never captured.

## Why a daily reading

Odoo purges inactive visitors every day, and their traces leave with them. A
sum computed on demand over `website.track` therefore measures what is left,
not what happened. The reading freezes each day while it is still there.

## What it does not claim

- The filtered series starts the day capture starts. Visitors from before have
  no captured agent: their views count as "unknown agent", never as humans.
  That is visible, and it is deliberate.
- A user agent can be forged. A crawler declaring itself a browser passes for a
  reader. The filtered series removes **declared** robots; it does not claim to
  count people.
- The historical ranking from the native counter is kept as it is, labelled
  raw. It is not retroactively corrected: nobody knows what it contained.

## Privacy

A user agent is a device identifier. The module keeps it in clear text so the
history can be reclassified when a new crawler appears. A retention period is
set through `bf_editorial_audience.ua_retention_days`: past that delay a daily
cleanup erases the string and keeps the verdict. At zero nothing is erased, and
that is then a decision to record in your own policy register.

## Installation

Depends on `bf_editorial`, `website`.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
