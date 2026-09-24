# Timer: Android app API (`bf_timesheet_timer_mobile`)

The server side of **Symbifox Chronomètre**, the Android app for the
[`bf_timesheet_timer`](../bf_timesheet_timer) timesheet timer. It pairs a phone
with the person's account and exposes the timer's gestures over a small JSON
API, with the same rules as the browser.

The app is distributed through the Symbifox F-Droid repository. This module is
what an instance needs for the app to have anything to show.

## Why an app, and not the installed web page

The timer page can be installed as a web app, but on Android it falls back to
the Odoo backend. Odoo 18's router rewrites any `/scoped_app` URL to `/odoo`
whenever `display-mode: standalone` is false, which is what happens every time
the browser only created a home-screen shortcut. No server setting changes
that.

## What it provides

- **Pairing** through the browser session the person already has: a one-time
  code valid 5 minutes, **PKCE S256 mandatory**, a bearer token stored **hashed**
  on the server, at most 10 paired devices per person, devices deactivated after
  180 days without a call.
- **`/bf_timer/mobile/v1`**: one read for the main screen (`/etat`: active and
  pending timers, recent and pinned tasks, day and week totals, rounding,
  description presets), task search and lookup, and the gestures: start, pause,
  resume, preview, **stop and log**, discard, pin.
- **Branding before pairing**: the public `/ping` returns the company name,
  colours and logo URL, so the app is painted in the right colours before the
  person signs in.
- **Local wipe deadline** (since 18.0.1.1.0): `/ping` also announces
  `wipe_after_days`, the number of days without a successful authenticated call
  after which the app erases its own data. A revoked token already makes the app
  wipe itself on its next call (401), but a phone left in airplane mode or never
  reopened would otherwise keep its data forever. Only an authenticated answer
  resets the countdown; reading the public `/ping` does not. The delay is the
  `bf_mobile.wipe_after_days` system parameter, shared with the other Symbifox
  mobile modules: **30 days** when the parameter is absent, empty or not a
  number, and `0` disarms the guard on purpose.

## Design rules

- **The phone never stops a timer without logging it.** `/chrono/apercu` reads
  what stopping would propose and changes nothing; `/chrono/enregistrer` stops
  **and** writes the timesheet in one request. Android kills apps in the
  background without warning; a stop sent on its own, followed by a
  confirmation that never arrives, would leave a stopped timer nobody logs.
- **Every gesture runs as the person**, with their access rights and companies.
  A token opens nothing more than the person could do in the browser.
- **Every bearer route runs in a savepoint.** An error returned as JSON leaves
  nothing written; Odoo does not roll back a request whose controller caught the
  exception.
- **A device's owner cannot be changed**, and revoking a device erases its token
  hash in the same write: reactivating the record does not bring the token back.
  Managers can read and delete devices; only system administrators can write.
- A pending pairing is created **inactive** and only becomes active once
  exchanged, so a forged request to `/auth/start` shows nothing in the person's
  device list.

## Requirements

- `bf_timesheet_timer` **18.0.1.12.0 or later**. Odoo's `depends` cannot express
  a version, so the module's `pre_init_hook` checks the database itself (the
  `first_start` column and the installed version) and refuses to install on an
  older timer.
- The person needs the *Timesheets / User* group.
- Allowed app redirect schemes are set by the `bf_timesheet_timer_mobile.schemas_appariement`
  system parameter (default `com.bluefoxconsultant.chronometre://`).

## Tests

`tests/test_appareil.py` covers pairing, PKCE, expiry, the device cap,
revocation and reassignment; `tests/test_api.py` plays the HTTP routes as an
ordinary internal user, including the refusals and the rollback of a failed
request; `tests/test_crochet.py` covers the install guard.

## License

Business Source License 1.1, see [LICENSE](LICENSE). Each version converts to
LGPL-3.0-or-later four years after its publication.
