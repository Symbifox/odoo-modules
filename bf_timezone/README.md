# Symbifox Timezone Utilities (`bf_timezone`)

> **Not a standalone module.** `bf_timezone` is a library that other Symbifox
> modules depend on. Installing it directly does nothing visible beyond adding
> a single *Default timezone* field under Settings — there is no app, no menu.

Shared, de-duplicated timezone helpers for Symbifox custom modules, plus a
single **configurable default timezone**.

Custom Odoo 18 CE module developed by
[Les services de consultation Blue Fox, Inc.](https://symbifox.com).

## Why

The same timezone code had been copy-pasted across several modules:

- UTC→local conversion (`pytz.utc.localize(dt).astimezone(...)`) in
  `bf_meeting`, `bf_appointment`, `calendar_nextcloud_sync`
- a Montréal/Auckland city-label map (`bf_meeting`)
- a Windows→IANA name map (`calendar_nextcloud_sync`)

Since **1.1.0** that map also covers Australia, New Zealand and Fiji. A Windows name missing from it does not fail loudly: resolution returns nothing and an `.ics` parser upstream can then read the local wall time as UTC, which lands an invitation whole hours off.
- a hardcoded `America/Toronto` fallback, everywhere

This module is the single source of truth for all of it.

## What it provides

`tools/tz.py` — pure, env-free functions:

| Function | Purpose |
|---|---|
| `normalize_name(tzid)` | Windows→IANA, strip leading `/` (Asia-Pacific names added in 1.1.0) |
| `tz_city(name)` | `America/Toronto` → `Montréal` (else last path segment) |
| `to_tz(dt, name)` | convert a (naive-UTC or aware) datetime to `name` |
| `resolve(candidates, fallback, validate=False)` | first usable tz name |
| `format_dual(dt, primary_tz, secondary_tz, fmt)` | `"… EDT (Auckland: …)"` |

`models/bf_timezone.py` — the `bf.timezone` `AbstractModel` wraps those and
adds `default_tz()`, which reads the `bf_timezone.default_tz` system
parameter. Call it from anywhere with an env:

```python
self.env['bf.timezone'].resolve([partner.tz, user.tz], validate=True)
self.env['bf.timezone'].format_dual(rec.date, client_tz, organizer_tz)
```

## Configurable default

**Settings → General Settings → Symbifox Timezone → Default timezone.**
This is the fallback used when no contact / user / calendar timezone is
available. It seeds to `America/Toronto`; flip it to `Pacific/Auckland` after
relocating and every consumer follows. The seed is `noupdate=1`, so a UI
change survives upgrades.

## Consumers

`bf_meeting`, `bf_appointment` and `calendar_nextcloud_sync` depend on this
module and route their timezone logic through it. Domain-specific code (ICS
`VTIMEZONE` generation, Google Calendar mapping, all-day end-date handling)
stays in those modules by design.
