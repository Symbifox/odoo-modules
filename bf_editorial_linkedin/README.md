# Editorial Workshop: LinkedIn (`bf_editorial_linkedin`)

The manual channel works and stays installed: the text is proofread in Odoo,
pasted on the network, and a button records the URL it came out at. This module
adds the automatic channel, for whoever accepts what LinkedIn asks in return.

## Three things to know before wiring it up

**1. You need a LinkedIn application.** Created at
`https://www.linkedin.com/developers/apps`, attached to a company page, and
granted the "Share on LinkedIn" product (scopes `w_member_social`, `openid`,
`profile`). Approval is not instant.

**2. The token expires after 60 days.** LinkedIn only issues refresh tokens to
approved programmes. In practice somebody pastes a new token every two months.
The channel therefore carries an expiry date, the credential check reminds you
of it, and a daily job warns a week ahead rather than letting a post fail on a
Sunday.

**3. No metrics come back.** Statistics for a **member** post are not exposed
by the API — they need an organisation page and other scopes. `_fetch_metrics`
returns an empty dict, which the frame reads as "this network does not give
them", not as zero. Returning zero would suggest a post nobody saw.

## The two API traps

**The post id does not come back in the body.** LinkedIn returns it in the
`x-restli-id` **header**, and the body of a successful creation is empty. A
connector that reads `response.json()` concludes failure and republishes on the
next pass. The "accepted without the header" case raises an error telling you
to check the feed before retrying, rather than guessing.

**`LinkedIn-Version` goes stale.** The versioned API requires a `YYYYMM`
header, and LinkedIn retires versions after roughly a year. Hard-coded, it
takes distribution down one morning with nothing having changed on your side.
It is set through the `bf_editorial_linkedin.api_version` parameter so a
version change does not need a deployment. The module ships `202608`, which
LinkedIn retires on 17 August 2027.

That default sits in a `noupdate` data file, so **upgrading the module will not
correct a database that already carries an older value** — on an existing
install, write the parameter yourself. The two places have to move together, or
a fresh install and an upgraded one end up on different versions.

## Installation

Depends on `bf_editorial_social`.

Paste the member token into the channel's application password, write down its
expiry date, then switch the channel's network from manual to LinkedIn. The
Fernet key of `bf_editorial_social` must be in place first, or writing the
credential is refused.

## Licence

BUSL-1.1. Internal use is free; providing a product or service to third parties
from this module requires a written agreement.
