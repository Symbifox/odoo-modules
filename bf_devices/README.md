# Symbifox Appareils — My Devices

One page that answers a question nobody could answer before: **which devices can reach my
things, and how do I cut them all off at once?**

## License

Business Source License 1.1 — see `LICENSE`. **Change Date**: on 2030-11-19 this version
converts to LGPL-3.0-or-later.

## The problem

Several in-house modules pair phones and desktop widgets — SMS archiving, email, the token
vault, and the Blue Fox OS desktop surfaces. Each one does it well, and each one does it on
its own. So each one has its own `/logout`, and that logout only clears its own record. A
person had no way to see the whole list, and no way to cut everything in one gesture.

## What this module does

It **adds no model and pairs nothing**. It reads the registries that already exist and turns
them into a single portal page, `/my/appareils`:

- one device per row, with the module it opens, what that token also opens, and its last
  activity;
- a button to remove one device's access;
- a button to remove them all at once.

🔴 **No new table, and that decision carries the rest.** Each module's own registry is the
source of truth for that module. A fourth table filled in parallel would have drifted the day
a module paired without going through it — that is to say immediately, since each has its own
pairing route.

⚠️ **No dependency on those modules either.** It installs on a tenant that has only email just
as well as on one that has all four, and it forces nobody to install what they do not need.
Sources that are absent are simply absent from the page.

## The sources it knows

| Source model | Label | What "remove access" clears |
| --- | --- | --- |
| `sms.archive.mobile.device` | Messages | `fcm_token`, `push_endpoint` |
| `bf.email.mobile.device` | Courriel | `push_endpoint` |
| `bf.otp.device` | Coffre de tokens | (the record is deactivated) |
| `bf.desktop.device` | Bureau | `token_hash` |

⚠️ **Each `efface` entry mirrors, field for field, what that module's own `/logout` sets back to
false.** Revoking from this page and signing out from the phone must leave exactly the same
record. Two gestures that claim the same thing and do not do the same thing are a trapdoor, not
a feature.

🔴 The desktop source clears `token_hash` where the three phone sources do not. That is not an
inconsistency: it is what the pairing module's own "Revoke" button does, and the two gestures
have to agree. The phone sources leave it alone because their own `/logout` leaves it alone.

## What it does not promise

⚠️ **Removing access does not wipe the phone.** The device keeps its token; the token simply
opens nothing any more. That is exactly what each module's `/logout` does, and the opposite of a
promise the software cannot keep.

## Requirements

Odoo 18, `portal`. The device modules themselves are optional: install the ones you use.

## Tests

`tests/test_registry.py` covers the registry (sources present and absent, what each one erases,
the capacities a token opens), `tests/test_portal.py` the page, and
`tests/test_coupe_circuit.py` the remove-everything gesture.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
