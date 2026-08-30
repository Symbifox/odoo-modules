# Music Licensing (`bf_music_licensing`)

Tracks what a Canadian establishment owes SOCAN and Re:Sound for the music it
plays, and above all what it might still owe: background music tariffs are
pending before the Copyright Board, and certification is retroactive.

**No music passes through this module.** No playlist, no channel, no streaming.
Serving audio would make the publisher a *background music supplier* under SOCAN
Tariff 16 and Re:Sound Tariff 3.A, with its own exposure. This module tracks
someone else's compliance; it never becomes a link in the chain.

## Two things most trackers get wrong

**"Two societies, two filings."** Not since July 2019. SOCAN and Re:Sound created
**Entandem**, which issues both licences in a single transaction and sends its
own renewal reminders. The module therefore follows **one** licence relationship,
not two.

**"Rates change every year."** The opposite. Everything touching background music
is **pending** before the Copyright Board, across several consecutive blocks. A
business pays today under a **proposed** rate that the Board can certify years
later, retroactively.

## The design rule

> One period carries **one** tariff row, holding both the **proposed** rate and
> the **certified** one.

A register that only held the rate in force could never say how much the bill
moved. That is why `rate_proposed` and `rate_certified` live on the same record
instead of on two successive ones.

## Models

| Model | Purpose |
|---|---|
| `bf.music.tariff` | The register: society, tariff, use, period, proposed and certified rates |
| `bf.music.establishment` | The place: floor area, uses, where the music comes from, licence account |
| `bf.music.licence` | One period: renewal deadline, payment proof, computed status |
| `bf.music.licence.line` | One tariff applied to one period, with what was actually paid |

## The four amounts

On every line, then rolled up to the period and the establishment:

| Field | What it says |
|---|---|
| `amount_proposed` | What the proposed rate commands, minimum floor included |
| `amount_reference` | What was paid when known, otherwise `amount_proposed` |
| `amount_at_risk` | The reference amount, **while the tariff is not certified** |
| `adjustment` | The real gap between the certified rate and what was paid |

`amount_at_risk` drops to zero when the Board rules, and `adjustment` takes over.
An amount can never sit in both.

## The gesture that produces the number

On an establishment, **Evaluate exposure** (`action_build_history`) creates one
licence period per missing year since 2020, with its tariff lines, and prices the
lot. A 250 m² clinic with four on-hold trunk lines, open 300 days a year, comes
out at roughly $7,576 paid between 2020 and 2026, every dollar of it under
tariffs that are not certified.

## The rate register

Read off the tariff texts published by the Copyright Board on 2026-08-30. Every
row carries the URL of the PDF its rate comes from, and a `rate_confirmed` flag
saying whether the rate was read from the text or merely assumed.

| Tariff | Period | Proposed rate | Minimum | Read |
|---|---|---|---|---|
| SOCAN 15.A | 2020-2021 | $1.53/m² (14.28¢/sq ft) | $117.75 | yes |
| SOCAN 15.A | 2022-2024 | $1.53/m² (14.28¢/sq ft) | $117.75 | yes |
| SOCAN 15.A | 2025-2027 | $2.32/m² (21.58¢/sq ft) | $177.99 | yes |
| SOCAN 15.B | 2020-2021 | $117.75 + $2.60/line | — | yes |
| SOCAN 15.B | 2022-2024 | $117.75 + $2.60/line | — | yes |
| SOCAN 15.B | 2025-2027 | $177.99 + $3.94/line | — | yes |
| Re:Sound 3.B background | 2023-2026 | 0.9650¢/m²/day | $140.93 | yes |
| Re:Sound 3.B on hold | 2023-2026 | $140.93 + $3.15/line | — | yes |
| Re:Sound 3.B | 2027-2031 | — | — | no |

**None of these tariffs is certified.** The redline text of the 2025-2027
proposal replaces $1.23/m² and a $94.51 minimum, the last **certified** amounts,
which date from the **2008-2011** period. An establishment has been paying under
successive proposals for fifteen years.

### Three things the tariff texts taught the model

1. **Re:Sound does not count the way SOCAN does.** Its basis is the area open to
   the public **multiplied by the number of days of operation**. Hence the
   `area_sqm_day` basis and the days-of-operation field.
2. **Music on hold is written "so much for the first line, so much for each
   additional line."** Multiplying the amount by the total would overcharge the
   first, hence `rate_base_proposed` beside the per-unit rate.
3. **SOCAN 15.A grants a half rate** to an establishment operating fewer than six
   months a year, and the minimum applies **after** that reduction.

### Still missing

* **The Re:Sound 3.B 2027-2031 block**, which touches no year before 2027. It is
  the only row left without a rate, and a test pins that.
* The two Re:Sound **fallbacks** when floor area cannot be established (0.5786¢
  per capacity place per day, then 0.3088¢ per admission) are in the text but not
  in the model: only the area basis is computed.

## The register updates from the file, not the interface

`data/music_tariff_data.xml` is `noupdate="0"` **deliberately**: these rows are
reference data, not user input, and an upgrade must refresh them when the Board
certifies. The corollary: a rate typed by hand in the interface onto a seeded row
**will be overwritten on the next upgrade**. Keep it in the file.

## The deadline engine

Computed, stored status; a daily scheduled action that raises an activity for the
manager group; a completion date that closes the file. All three tariff texts set
payment no later than **January 31 of the year covered**.

The scheduled action ships **disabled**. Enabling it on a fresh database would
raise activities on files nobody has reviewed yet.

## Access

Two groups, User (read) and Manager (write, keeps the rate register and receives
the deadline reminders). Manager implies User. Multi-company record rules on all
three main models. An internal user in neither group sees neither the menu nor
the records.

## Requirements

* Odoo 18 Community
* Additional Odoo module dependencies (manifest `depends`): `mail`

## Licence

Business Source License 1.1, converting to LGPL-3.0-or-later on 2030-08-30. See
`LICENSE`.
