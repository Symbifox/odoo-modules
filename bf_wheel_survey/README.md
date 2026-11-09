# Prize Wheel ↔ Surveys (`bf_wheel_survey`)

Installs itself when `bf_wheel` and Surveys are both there.

## What Odoo already does, and what this does not redo

Odoo 18's Surveys app **already** holds the Kahoot-style live session: the
"Live Session" survey type, a session code, a projected QR code, people joining
through `/s/<code>` with a nickname, a per-question timer, a speed bonus, the
answer histogram rising live, text answers on screen, leaderboard and podium.

What it does not have, in any of its 686 modules: **a random draw**.

## What the bridge adds

Two more fillings for the wheel:

- **The attendees of the live session**: the wheel draws among the people who
  joined the current session, each under the nickname they gave. One attendee,
  one slice: luck depends neither on score nor on arrival time.
- **The answers to a question**: one slice per answer given and, optionally, a
  slice as wide as the answer is frequent. The wheel becomes the map of what
  the room answered.

A *Prize Wheel* button appears on the survey while the session runs.

## What is measured and not guessed

- **Yesterday's session does not count.** The list is bounded by
  `session_start_time`, the way Odoo itself bounds a session's answers.
- **Attendees are re-read at every spin**: whoever joins between two draws is in
  the second one.
- **No nickname, no slice**: a mute slice names nobody on screen. The same
  nickname twice is not two chances, and a test entry does not play.
- **A session that has not started says so** instead of spinning on nothing.
- ⚠️ A nickname is personal information as soon as it names someone. The
  registry keeps the candidate list of a draw and prunes it at the end of the
  wheel's retention delay, together with the contact details.

## License

BUSL-1.1, Change Date **2030-11-08**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
