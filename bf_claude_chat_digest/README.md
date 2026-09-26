# Gen: Claude usage in the daily digest (`bf_claude_chat_digest`)

Adds a **Claude usage** section (*Consommation Claude*) to the daily digest of
`daily_todo_digest`, fed by the subscription readings that `bf_claude_chat`
stores in `claude.account`.

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
- the extra-credit usage, when the account has purchased credits.

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

## Each recipient sees only what they may see

Accounts are readable by administrators only. The digest is sent from a
scheduled job, so the section is read **as the recipient**, never with superuser
rights: a recipient who is not an administrator gets no section at all.

## Details that matter

- **Account names are escaped** before they reach the HTML email.
- **The section sits in its own table row.** The digest template's insertion
  marker lies between two `<tr>`; a bare block inserted there would be moved out
  of the table by any HTML5 parser and shown above the card.
- A per-digest switch, *Inclure la consommation Claude*, turns the section off.

## Tests

```sh
odoo -d <db> -u bf_claude_chat_digest --test-enable \
     --test-tags=/bf_claude_chat_digest --stop-after-init
```

## License

Business Source License 1.1, see `LICENSE`. Each version reverts to
LGPL-3.0-or-later at its Change Date.
