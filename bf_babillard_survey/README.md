# Noticeboard: Odoo surveys (`bf_babillard_survey`)

A bridge between the noticeboard (`bf_babillard`) and Surveys (`survey`). It installs itself as soon as both modules are present.

The noticeboard carries polls of its own: one question, a few choices, one click. This bridge serves the other need, the questionnaire. An Odoo survey has pages, sections and scored questions, and it is answered elsewhere. The feed announces it and gives its link; it does not replay it.

An editor announces a survey from its form, with the **Announce on the noticeboard** button. It is a deliberate gesture, not an automatic one: an open questionnaire is not necessarily company news, and the feed is the one place everybody reads.

The card is posted once. Announcing the same survey again opens the existing card instead of posting a second one.

## And the live session, with its code

A live session is not the survey. It happens now, it is joined by typing a short
numeric code, and the survey's own start link does not even lead to it. A second
button, **Announce the session on the noticeboard**, appears only while a
session is open: the card then carries the code and the short join link.

Without it, nothing announces an open session to the company — the host writes
the code in a message, or nobody arrives.

Two things are measured there, and neither is housekeeping.

- **The second session rewrites the card.** The feed keeps one card per source,
  so a session held a month later cannot post a second one. It rewrites the
  survey's card with today's code and lifts it back to the top of the feed. A
  card still showing last month's code would be worse than no card at all.
- **The expiry date is cleared on the rewrite.** The nightly cron expires every
  published card whose date has passed. A card that expired on the 3rd and was
  republished this morning for the 10 o'clock session dropped out of the feed at
  the cron's next pass — during the session.

## What it refuses, and why

A survey restricted to invited people is not announced. Its start link only works with the personal invitation that carries the answer token, so a card read by the whole company would lead nowhere. The bridge says so and asks for the survey to be opened to anyone with the link first.

A **certification** is refused for the session button as well: Odoo itself declines session codes on a certification, so the announced code would lead nobody anywhere.

**One card per survey, across every company.** An Odoo survey carries no company; a feed does. If the survey's card already sits on another company's noticeboard, the button refuses and names that company, instead of quietly moving the card over.

Publishing to the noticeboard is reserved to the noticeboard's editorial group. The check lives in the method, not in the button: a `groups` attribute only guards a screen.

## Dependencies

`bf_babillard`, `survey`.

## License

BUSL-1.1, Change Date **2030-11-08**, Change Licence LGPL-3.0-or-later. Internal
use stays free; reselling or hosting it for a third party needs a written
agreement. See `LICENSE`.
