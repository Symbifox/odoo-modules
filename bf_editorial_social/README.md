# Editorial Workshop: Social Networks (`bf_editorial_social`)

The distribution frame for `bf_editorial`. It speaks to no network in
particular: each network ships as its own connector module implementing the
same interface.

## The problem

Publishing an article and announcing it are two different jobs, and the second
one is where the work leaks out of the system. The blurb gets written in a chat
window, the link gets shortened somewhere else, the posting happens by hand,
and nothing that comes back — clicks, likes, replies — ever finds its way to
the article it belongs to.

Odoo Community has no answer here. Social Marketing is Enterprise only, and it
is built around campaigns rather than around an editorial calendar.

## What it does

**A channel per account.** The network, the handle, the language it publishes
in, its character limit, and its credentials encrypted outside the database.

**Blurbs.** One text per article, per channel and per language, with the
network's character limit applied as you type and the same editorial QA that
`bf_editorial` applies to prose: em-dashes, banned phrases, length.

**A deferred queue.** A post leaves at the appointed time, or not at all if its
article's pre-flight gate still refuses. Queueing resolves the tracked link
there and then, so the exact text that will go out is readable before anyone
approves it.

**No-duplicate guarantee.** This is the point of the module. A periodic job
resuming a queue after a network cut will republish unless something stops it.
Here every post carries a per-channel idempotency key, the queue reserves it in
its own transaction before any outbound call, and the remote id is written the
moment the answer comes back.

**Measurement, as a dated series.** Likes, reposts, replies and clicks come
back onto the editorial entry as observations with dates rather than as a
counter that only ever moves forward. A network that does not expose a metric
returns nothing, which the frame reads as "not available" — never as zero.

## What it does not do

- No network is reachable without its connector module.
- It does not write the blurbs and it does not pick the hours.
- Credentials are never stored in clear text, and the encryption key never
  lives in the database: it comes from the environment (`BF_SOCIAL_FERNET_KEY`)
  or from `odoo.conf` (`bf_social_fernet_key`).

## Things worth knowing

**The link is resolved at queue time, not when the blurb is written.** The
tracked short link does not exist before then, and hard-coding the long URL in
the body would lose the per-channel attribution. On a network fed by hand the
link is appended to the body — nobody reads a `link_url` while pasting text —
and on a network that renders a link card it is passed separately, which saves
characters where the limit is tight.

**One tracked link per article, channel and language.** `link.tracker` carries
a uniqueness constraint on (url, campaign, medium, source); the module reuses
an existing tracker rather than creating a second one, so clicks stay
aggregated across re-queues.

**A blurb's character count includes what will be appended.** The count shown
adds the hashtags and, where the link goes in the body, the length of the
article URL — the long one, not the short one. Overestimating is the only
direction in which being wrong is harmless.

## Installation

Depends on `bf_editorial`, `link_tracker`, `utm`.

Set the Fernet key before creating a channel, or writing a credential will be
refused. Install at least one connector module, or the manual channel
(`bf_editorial_manual`), before expecting anything to leave.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
