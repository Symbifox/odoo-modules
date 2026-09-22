# Babillard : sondage de disponibilités (`bf_babillard_appointment_poll`)

A bridge between the noticeboard (`bf_babillard`) and the availability poll (`bf_appointment_poll`). It installs itself as soon as both modules are present.

An availability poll addresses people who have no account: each of them answers through a link of their own. The noticeboard addresses the company. This bridge does the one thing that honestly crosses that boundary, which is to announce on the feed that a poll is looking for answers.

An editor announces an open poll from its form, with the **Announce on the noticeboard** button. A poll still in draft, already scheduled or cancelled is refused: only a poll that is actually collecting answers is worth announcing. The card carries the closing date when there is one, and drops off the feed with it.

## What the card never carries

**The voting link.** It is personal to a participant, and posting it on a card the whole company reads would lend one person's voice to everybody. When the poll offers open sign-up, the card carries that link instead, which is public by construction. When it does not, the card announces without a link and points to the organiser.

Publishing to the noticeboard is reserved to the noticeboard's editorial group. The check lives in the method, not in the button.

## Dependencies

`bf_babillard`, `bf_appointment_poll`.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
