# Live Session Base (`bf_live_base`)

No screens, no models, no menus. This module carries what the live-session
family shares: **a palette of twelve measured hues**, **the calculation that
picks the ink written on them**, and **a brand ramp** for the blocks of a
podium.

## Why

Readability on a projected screen is a measurement, not a taste. A label read
from the back of a room either reads or is useless, and the contrast between a
text and its background is given by WCAG: the minimum for normal text is 4.5:1.

Every hue in the palette therefore sits at a lightness where one of the two
house inks reaches that minimum. Four hues picked by eye (`#e05263`, `#8e6fd8`,
`#d96a3b`, `#6c7a89`) fell between the two, too dark for the charcoal ink and
too light for the white one, and carried a label at 3.5:1. A test re-reads the
minimum over the twelve hues on every upgrade.

## What it exposes

`odoo.addons.bf_live_base.models.color_utils`:

| | |
|---|---|
| `PALETTE` | The twelve hues, the house blue first. |
| `palette_color(index)` | The colour of a rank, the palette cycling forever. |
| `text_color(background)` | Whichever of the two inks contrasts most with it. |
| `contrast_ratio(a, b)` | The WCAG ratio, from 1:1 (identical) to 21:1. |
| `relative_luminance(colour)` | The WCAG relative luminance. |
| `normalize_hex`, `is_hex` | `#ABC` becomes `#aabbcc`; anything else is refused. |
| `brand_ramp(colour)` | Three steps of one tone, for the blocks of a podium. |
| `darken`, `lighten`, `readable` | The three gestures the ramp is made of. |

The two inks are `DARK_TEXT` (`#2d3031`) and `LIGHT_TEXT` (`#ffffff`).

## The brand ramp

Three podium blocks are not three palette colours: they are one tone stepping
down, or the podium looks like a chart. `brand_ramp` computes them from any
hue — the company's own, when it has one.

Two things are measured there and cannot be guessed.

- **A computed step can land between the two inks.** It is not a given: four
  palette hues picked by eye had landed at 3.5:1. `readable` pushes the step in
  the ramp's direction, by 8% at a time, until one ink reaches 4.5:1. The
  company's hue stays its own; it only darkens.
- **A near-black hue does not step down.** Below a relative luminance of 0.12
  the ramp climbs towards white: `#000000` gives `#000000`, `#595959`,
  `#9c9c9c`, three steps you can tell apart, instead of three identical blacks.

## ⚠️ The threshold everyone quotes would be wrong

`text_color` compares both contrast ratios instead of crossing a threshold. The
threshold found everywhere (luminance 0.5, or 0.179 against pure black) assumes
a black ink. The house ink is charcoal, whose break-even point is 0.238: at 0.5,
the house blue would receive white, which reads on it at 2.6:1.

## License

BUSL-1.1, Change Date **2030-11-08**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
