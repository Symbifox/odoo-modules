# Prize Wheel (`bf_wheel`)

A wheel you project in front of a room, spin, and watch stop on a name. The
attendance draw of a workshop, the pick of the next topic, the forfeit of a
training session.

## What the module holds

| | |
|---|---|
| **The wheel** | Named segments, each with an integer weight and a colour. |
| **The screen** | Full frame, charcoal background, fixed needle on top, the wheel turning under it. |
| **The registry** | One row per spin: date, who spun, winner, candidates, total weight. |
| **The person drawn** | Optionally their name and one contact detail, erased after a retention delay. |

## The server draws, the animation catches up

The animation picks nothing: it joins a result already taken. The server draws,
writes the spin to its registry, then returns to the screen **the winning rank
and the exact list it drew from**. The screen redraws that list before turning.

That is what separates a draw from an animation. A wheel that picks in the
browser cannot be verified, can be replayed until it gives the wanted result,
and cannot carry a prize.

The list travels with the result for a precise reason: between the moment the
screen opened and the click, a candidate may have arrived or left. The wheel
you watch must be the one the result came out of.

## The weight

Each segment carries an integer weight, at least 1. A weight of 3 takes three
times more room on the wheel and comes up three times more often: **the slice
drawn and the odds drawn are the same number.** The draw runs on an integer
within the total weight, then walks the list: no floats, so no rounding that
would lend a hair of extra luck to the last segment.

## The colours

Twelve hues, and each label is written in whichever ink contrasts most with it
(white or charcoal `#2d3031`). All twelve reach the WCAG minimum of 4.5:1: four
hues picked by eye fell at 3.5:1 and were moved.

The palette and the calculation live in **`bf_live_base`**, the family's base
module: three modules use them now, and wanting a word cloud in a live session
should not force a wheel upon you.

The ink **leaves the server with each candidate**. The choice is not made by a
luminance threshold: both contrasts are compared and the better one kept. The
threshold everyone quotes (0.5, or 0.179 against pure black) assumes a black
ink; with the house charcoal the break-even point is 0.238, and a blue segment
would receive white, which reads on it at 2.6:1.

## The registry, and what leaves it

A registry row is never rewritten and never deleted by hand. Only the contact
details of the person drawn are added, once, and correcting them takes a
manager.

⚠️ **Deleting the wheel takes its registry with it.** The cascade happens in the
database, and PostgreSQL does not go through the model's guard. It is the only
path that empties the registry, and it is deliberate.

The name and contact detail of a person drawn are personal information. The
wheel carries a retention delay (90 days by default); past that delay, a
scheduled job erases the name, the contact detail **and the list of candidates**,
and marks the row as pruned. The winner, the date and the counts stay: that is
the proof of the draw. At zero, nothing is erased automatically, and the screen
says so.

## Access

| Group | What it can do |
|---|---|
| *Prize Wheel / User* | See the wheels, spin them, read the registry, note a person drawn. Implied for every internal user. |
| *Prize Wheel / Manager* | Create and edit wheels, correct a contact detail already noted. |

Spinning is not writing: the registry is written in `sudo`, outside the caller's
context, after the read right and the group have been checked.

## Fillings

On its own the wheel draws among its own segments. `bf_wheel_survey`, which
installs itself alongside Surveys, adds **the attendees of a live session** and
**the answers to a question**.

## Known limits

- The projected screen is meant for one wheel at a time, full frame; it is not
  a dashboard widget.
- A browser tour (`start_tour`) is skipped wherever the Odoo image carries
  neither `websocket-client` nor Chrome, which is most images: the screen is
  therefore covered by an external browser walk-through rather than by a tour.

## License

BUSL-1.1, Change Date **2030-11-08**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
