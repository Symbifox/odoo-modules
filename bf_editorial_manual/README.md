# Editorial Workshop: Manual Channel (`bf_editorial_manual`)

Not every network can be published to by API. LinkedIn, for one, reserves
posting to a company page for its *Community Management API*: registered legal
entity, verified page, two-stage app review. Until that door opens, publishing
is a copy and paste.

This module does not pretend to get around it. It gives the network a channel
in good standing — a written blurb, checked by the house QA, measured against
the network's real character limit, attached to its article with a tracked
link — and then it stops exactly where automation actually stops.

## What it changes in practice

- A blurb is written for this channel like for any other, by hand or by Gen.
- The editorial QA applies: em-dashes, banned phrases, length.
- The tracked link is resolved, so clicks stay attributable even when the post
  went out by hand.
- Sending refuses with a message that says what to do, rather than failing
  silently. Once the paste is done, a button records the post as published
  against the URL it came out at — and refuses that URL if it is the article's
  own, which is the mistake everyone makes once.

## The day the API opens

Change the channel's network. The blurbs already written stay. Nothing composed
here is lost in the switch.

## Installation

Depends on `bf_editorial_social`. No credentials, no configuration.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
