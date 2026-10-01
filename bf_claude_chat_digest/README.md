# Gen in the daily digest (`bf_claude_chat_digest`)

Adds two sections to the daily digest of `daily_todo_digest`: **Claude usage**
(*Consommation Claude*), fed by the subscription readings that `bf_claude_chat`
stores in `claude.account`, and **Gen conversations to follow** (*Conversations
Gen à suivre*), the recipient's own conversations that are waiting for
something.

A separate satellite, so that neither module imposes the other: install it when
both `bf_claude_chat` and `daily_todo_digest` are present. It does not
auto-install, because accounts only exist where a usage probe reports.

## What the section says

One row per active account:

- the account's state, judged against its own thresholds (*under thresholds*,
  *cap in sight*, *balance about to be lost*, *no usable reading*);
- how much of each measured window is used: the 5-hour session, the week, and
  the per-model weeks when the server reports them;
- when each window resets, in the accounts' reference time zone, so the answer
  does not change with the reader's own time zone;
- the extra-credit usage, when the account has purchased credits;
- the account's email address, as the probe read it, so that two subscriptions
  sharing a name can be told apart; a reading without it keeps the last one;
- the time left before each reset, in days, hours and minutes.

## A counter, not an alert

Unlike the other digest sections, this one does not go quiet on calm days: the
point is to read the numbers every morning. It only stays out of the digest when
there is no account at all.

## A frozen counter is not a reassuring counter

An account the probe can no longer read keeps its last windows, and its state
would still say *under thresholds*. The section therefore judges the freshness
of the reading itself: a reading older than six hours, or a probe in error,
turns the row red with its diagnosis, and the opening line says so. A window
whose reset time has passed since the reading is flagged rather than shown as
current.

## Gen conversations to follow

When the tenant has turned on *Conversations aim to close* in `bf_claude_chat`,
each recipient gets their own conversations that are waiting for something, in
three groups: **to close** (Gen judged them done, or they went dormant),
**waiting for you**, and **followed up, no answer** (the nightly pass followed
them up). Each line carries Gen's one-line reason, how long ago it last moved,
and a link that opens that conversation (`?gen_session=<id>`). The section stays
quiet when nothing is waiting.

The same moment sends the **daily notification** to the recipient's phone (one
a day, local day, even with several digests), but only from the scheduled job:
"Send now" and "Send a test" never push anything. Unticking *Inclure les
conversations Gen à suivre* turns off both the section and the notification.

The section is read **as the recipient** and filtered on their own
conversations: an administrator, who may read every conversation, still only
gets theirs. A portal recipient gets nothing, and a failing section never takes
the whole email down.

## Each recipient sees only what they may see

Accounts are readable by administrators only. The digest is sent from a
scheduled job, so the section is read **as the recipient**, never with superuser
rights: a recipient who is not an administrator gets no section at all.

## Details that matter

- **Account names are escaped** before they reach the HTML email.
- **The section sits in its own table row.** The digest template's insertion
  marker lies between two `<tr>`; a bare block inserted there would be moved out
  of the table by any HTML5 parser and shown above the card.
- Per-digest switches, *Inclure la consommation Claude* and *Inclure les
  conversations Gen à suivre*, turn each section off.

## Tests

```sh
odoo -d <db> -u bf_claude_chat_digest --test-enable \
     --test-tags=/bf_claude_chat_digest --stop-after-init
```

## License

Business Source License 1.1, see `LICENSE`. Each version reverts to
LGPL-3.0-or-later at its Change Date.
