# Hosting system updates: digest section (`bf_hosting_patch_digest`)

Adds a **fleet updates** section (*Mises à jour du parc*) to the daily digest of
`daily_todo_digest`, fed by the reports of `bf_hosting_patch`.

A separate satellite, so that neither module imposes the other: install it when
both `bf_hosting_patch` and `daily_todo_digest` are present. It does not
auto-install.

## What the section says

One line per state worth a sentence, in the module's order of severity:

| State | Meaning |
|---|---|
| `stale` | the system has not reported for 48 hours |
| `security` | security fixes are waiting |
| `reboot` | the system needs a reboot |
| `blind` | the package count is unknown (the package manager failed) |

Each line counts the systems in that state and names up to eight of them, as
*machine / system*, so a dual-boot laptop says which side is behind. The rest is
counted, not listed: an email is not a work queue, it tells you whether the
screen is worth opening.

## Silence has two meanings, and only one is good

The section stays out of the digest when there is nothing to say. A section
repeating every morning that all is well stops being read.

But staying quiet because the machines stopped talking would say "nothing to
report" when the true sentence is "nobody is measuring". A silent system
therefore **always** brings the section out, with an opening line that says a
missing report is not good news. This is `bf_hosting_patch`'s own rule applied to its
own surface: a missing report is an alert, never a silence.

## Each recipient sees only what they may see

The digest goes to every person on its configuration, with no access condition
of its own. The section is therefore read **as the recipient**, never with
superuser rights: someone without hosting access gets no section at all, and a
hosting user bound to some clients only sees those clients' systems, under
`bf_hosting_patch`'s record rules.

## Details that matter

- **System names are escaped.** They come from the agent, hence from the
  network, and they end up in an HTML email.
- **The section sits in its own table row.** The digest template's insertion
  marker lies between two `<tr>`; a bare block inserted there would be moved out
  of the table by any HTML5 parser and shown above the card.
- A per-digest switch, *Inclure l'état de mise à jour du parc*, turns the
  section off.

## Tests

```sh
odoo -d <db> -u bf_hosting_patch_digest --test-enable \
     --test-tags=/bf_hosting_patch_digest --stop-after-init
```

Ten tests: silent when all is well, always present when a system is silent,
the heading in singular and plural, security and unknown counts shown, the
switch, no section without hosting access, only the recipient's clients named,
name escaping (including past the eight-name limit), and the table-row
insertion.

## Licence

LGPL-3.0-or-later. See `LICENSE`.
