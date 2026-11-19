# Symbifox Desktop Pairing

The pairing layer for **desktop** surfaces on Blue Fox OS. It displays nothing and
synchronises nothing: it answers one question, "is this widget allowed to speak for this
person?".

First client: the *Symbifox Sticky Notes* Plasma widget.

## License

LGPL-3 — see `LICENSE`.

## Why a layer of its own, and not a route inside the notepad

The notepad already accepts tokens from two modules that pair phones
(`bf_sms_archive`, `bf_email_management`) without depending on either. The desktop
surfaces that will follow — a calendar, a timer — will have the same need. So pairing
lives apart, and each surface recognises it the way it already recognises the other two:
by looking for the model in the registry.

**This module depends on nothing but `base` and `web`**, and no surface depends on it. A
tenant without Blue Fox OS does not install it; a tenant that installs it without the
notepad has a layer waiting.

## The flow: device authorization (RFC 8628)

```
widget                         browser                       Odoo
  │  POST auth/device/start ───────────────────────────────────▶
  │  ◀─── device_code, device_secret, verification_uri_complete
  │
  │  opens the URL ──────────▶ GET auth/device/approve ───────▶  auth="user"
  │                                  (password, SSO, TOTP)
  │                            ◀── consent page
  │                            POST auth/device/consent ──────▶  CSRF
  │
  │  POST auth/device/poll ────────────────────────────────────▶
  │  ◀─── 202 authorization_pending … then the token, once
```

**Why not the phone flow.** A QML plasmoid has no embedded browser and no URL scheme it
could own, and opening a local port to catch a redirect would let any other process on the
workstation race it. Nothing comes back to the widget by redirect here: the widget asks.

**Why there is no code to type.** The RFC provides for a short code, read aloud or typed on
another device. We only ever serve `verification_uri_complete`. A flow that never asks you
to type a code makes any code a third party sends you suspect by construction. The
capability therefore lives in the URL: long, unreadable, single use.

🔴 **That was not enough.** The consent page shows the name and login of whoever READS it,
whichever person opened the pairing; and the workstation label comes from the request, so an
attacker could write a plausible one there. Nothing let you recognise YOUR widget: open a
pairing, send the link ("your sticky note lost its pairing"), and two clicks later the
attacker holds a token on the victim's notepad.

**Number matching** closes that path without asking anyone to type: the widget shows two
digits, the page offers three, you click the one on your screen. A pairing you did not start
fails two times out of three, and a wrong choice **destroys** the request — letting someone
retry would make the three numbers pointless. The number is derived from the pairing code, so
nothing extra is stored, and the order of the three is stable from one display to the next so
an honest person never doubts what they saw.

## What the module refuses

| Action | Answer |
| --- | --- |
| An unknown surface | `unknown_app`, nothing is created |
| An expired or replayed code | the request is **destroyed**, not left in place |
| A poll with the wrong secret | the request is destroyed; the token was never revealed |
| A poll faster than the interval | `slow_down` |
| A second poll after delivery | nothing: the token comes out once |
| An archived or portal account | the token no longer resolves |
| 90 days without a call | the token revokes itself |
| Creating or rewriting a row by hand | `AccessError`, writes included |

🔴 **The brake on the public route.** `auth/device/start` is public and it *creates a row*.
Without a brake, anyone who can reach the instance fills the table. The module first purges
dead requests, then refuses beyond **five requests in flight per caller**.

⚠️ There is NO low global ceiling, and that is a correction: a shared bound of fifty turned
this route into a **pairing switch**. Ten source addresses — a single IPv6 /64 supplies as
many as you like — and nobody in the tenant could pair a widget any more, with a message that
blamed their own widget. The absolute bound still exists, very high, so the table cannot grow
without end.

⚠️ The **content type is required** (`application/json`). Without it, a POST in `text/plain`
is a "simple request" as far as CORS is concerned: any booby-trapped web page would burn a
slot from each of its visitors' addresses, with no preflight, and the per-caller brake would
protect nothing.

The caller's address is kept only as a **keyed** hash (HMAC with the database secret), and
only for the lifetime of the request. A bare SHA-256 of an IPv4 address is 2^32 candidates:
it stored the address.

## Where a person sees their devices

In **"My devices"** (`/my/appareils`, module `bf_devices`), next to their phones: the page
that already exists and revokes everything in one gesture. This module's own menu, under
Settings, is for administrators and only lets them look at the fleet.

⚠️ `bf_devices` 18.0.1.3.0 declares `bf.desktop.device` as a fourth source, naming
`token_hash` in its erase step: revoking from the page and revoking from the record must
leave **exactly** the same row. Two gestures that claim the same thing without doing it are a
trapdoor.

## What is left

- The token lives in the widget's Plasma configuration, in clear text in the person's
  profile. KWallet is the next step.
- No wake-up: surfaces poll. A push transport would add a topic field here, and nothing else.

## What a surface accepting these tokens must do

The notepad does it; the next surface will have to as well.

- **Call `_touch_last_seen()`** rather than writing `last_seen` through the ORM.
  🔴 An UPDATE of the device row inside every call's transaction is a known incident: two
  simultaneous calls from the same device fight over the row, the second fails, and the
  person's action is rolled back over a timestamp. A widget that polls every three seconds
  makes that a daily event.
- **Let `PG_CONCURRENCY_EXCEPTIONS_TO_RETRY` through**: Odoo replays the request on a write
  conflict, but only if the exception reaches it. Catching it as an "unexpected error" turns a
  one-millisecond conflict into a permanent 500.
- **Never rely on a record rule to restrict** what a route returns. The rules of one model
  combine with OR, and a module often has an "admin sees all" rule: an empty domain then
  returns the whole database. Name what you want.

## Tests

`tests/test_pairing.py` covers the flow end to end (start, consent, poll, number matching,
destruction on a wrong choice, the per-caller brake, the required content type, expiry and
self-revocation). `tests/test_integration_notes.py` covers what a surface sees when it accepts
one of these tokens.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
