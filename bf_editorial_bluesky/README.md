# Editorial Workshop: Bluesky (`bf_editorial_bluesky`)

The Bluesky connector for the distribution frame. It implements the
`bf.social.connector` contract and nothing else: no queue logic, no editorial
rule.

## Why Bluesky first

It asks for the least. An app password created from the account settings, no
API application, no app review, no paid tier. It validates the abstract
interface at the lowest risk before the networks that ask for more.

## What the connector does

- Opens a session with an **app password**, never the account password. The
  session is never cached: the access token lasts about two hours, and a
  periodic job holding one all day fails silently from the third hour on.
- Publishes the text with its **link card** — the article's title, description
  and thumbnail — so the link does not sit bare in the feed.
- Marks up **links and hashtags correctly**. The AT protocol expects positions
  in UTF-8 **bytes**, not characters. A French post is full of accents; count
  in characters and every facet after the first accent is off by one. This is
  the classic source of error, and the module has a test for it.
- Fetches likes, reposts and replies back.

## What it does not do

- Bluesky publishes no impression count. The impressions metric stays empty,
  and that is the network's decision, not the module's.
- The text is capped at 300 characters, counted in graphemes by the network.
  The module applies the limit as you type.

## Things worth knowing

The link does **not** go in the body on this network: it travels as a link
card, built from the post's tracked URL. That saves the characters a short link
would cost, out of 300.

## Installation

Depends on `bf_editorial_social`.

Create an app password in the Bluesky account settings, paste it into the
channel, and check the credentials — the check refuses if the session opens on
a different handle than the one the channel declares.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
