# Symbifox Appointment Polls (`bf_appointment_poll`)

Availability polling for *Symbifox Appointment*: the organizer proposes slots
that are **already free in their own calendar**, each invitee answers slot by
slot, and the organizer turns the winning slot into a real booking in one
click.

## Features

- **The organizer proposes, the participants vote.** Candidate slots come from
  `resource.booking.type._bf_candidate_slots()`, so every slot offered is one
  the organizer can actually honour. Letting everyone paint freely on the
  organizer's calendar makes the intersection unmanageable and freezes the
  agenda.
- **Three answers, not two**: Yes / If need be / No. "If need be" is what
  unblocks most polls in practice.
- **Required vs optional participants.** A slot stops being viable as soon as a
  required participant answers No, and its calendar hold is released
  immediately.
- **Holds that do not block the agenda**, unless you ask for it. When enabled,
  each candidate slot places an event marked `show_as='free'`: the organizer
  sees the poll in progress, while public bookings keep flowing over those
  hours. The `blocking` level really does close the slot, and is never the
  default. A hold is placed slot by slot, as each one is picked; in `blocking`
  mode, expect every picked slot to leave your public booking page for the
  duration of the poll, bounded by the per-poll slot ceiling.
- **A public voting page** built for a phone, one tap per slot, rendered in the
  **respondent's own time zone** — taken from their browser, overridable from a
  dropdown above the slots, and falling back to the timezone configured in
  Settings. The whole page moves with it, not just a label: ticking an hour you
  have to convert in your head is the surest way to collect wrong answers. The
  choice is remembered, so the confirmation lands in the same time zone as the
  page where the person answered.
- **The grid comes first.** Once somebody has proposed, a newcomer sees what is
  already on the table before being offered the pool, which folds behind
  "Suggest other times (x/y left)". The overlap only forms if you see what is
  there. The counter crosses **both** ceilings — yours and the poll's — because
  a counter that promises more than will be accepted is worse than none.
- **Answers are locked behind a code.** Self-signup identifies people by an
  address they typed, and sign-up is idempotent on it: knowing the link and
  somebody's address used to be enough to read *and* change their answers.
  Someone who has already answered now lands on a read-only page — the live
  state of the poll stays visible — and changing anything asks for a six-digit
  code sent to their address. The unlock lives in the session, never in the
  URL, and the code is stored only as an HMAC bound to the participant.
  Invitees named by the organizer keep their personal link untouched: it
  reached them by email, which already proves the address.
- **Invitation and reminder emails** under the tenant's brand.
- **Closing is a façade.** It calls the parent's `_bf_create_booking()`, so the
  calendar event, the ICS attachment, the video room and the reminders all
  follow the usual path. There is no parallel pipeline to maintain.
- **Booking asks which slot.** The button opens a chooser that ranks the slots —
  still viable first, then complete, then the number of Yes, an "if need be"
  counting half — and preselects the best one, with who answered what beside
  each. Each slot row also carries its own "book here" shortcut. Calling the
  method without a slot takes the **best ranked** one, never the earliest.
- **Everyone is told when it is set.** Each participant gets their own branded
  confirmation with the `.ics` attached, in their own time zone. The same mail
  is what the "Resend invitations" button sends.
- **The poll's participants land on the calendar event itself**, not only on
  the booking. Closing re-reads the database once the creation is over and
  adds whoever is missing, silently — the branded confirmation already carries
  the `.ics`, so Odoo's own invitation would be a second email for one
  meeting. The addition is purely additive: anyone added by hand on the event
  stays, and if the write does not take, it is logged rather than swallowed.

## The scheduled job ships disabled

`data/poll_cron.xml` installs the maintenance job with `active=False`, and
`noupdate="1"` protects that choice — otherwise every module upgrade would
reset it to the shipped state and switch it off for a tenant who had turned it
on.

The job does two things: chase participants who have not answered, and close
polls whose deadline has passed. The first one writes to third parties, so
**enabling it is an operational decision, taken per tenant**, once both email
templates have been reviewed in that tenant's language and brand.

Two reminders, at D+2 and D+5, then it stops: past that, response rates fall
rather than rise.

## Dependency

Requires `bf_appointment` **18.0.2.40.0 or later**. It consumes four extension
points:

| Extension point | Used here for |
|---|---|
| `resource.booking.type._bf_candidate_slots()` | offering slots without reserving them |
| `resource.booking.type._bf_create_booking()` | turning the winning slot into a booking |
| `bf_source` / `bf_source_ref` | provenance, with no typed relational field |
| `bf_rate_limit()` | capping the public voting route |

⚠️ Odoo's `depends` cannot express a minimum version. Check the installed
version of `bf_appointment` before installing this module.

The dependency runs one way only. `bf_appointment` references no model from
here, and two tests lock that down — one on each side. A relational field
pointing at a satellite model would make the satellite a *hard* dependency,
resolved at registry load, and the defect would only ever show up on a fresh
install.

## Fixed in 18.0.1.2.3

- The public page mixes a **dark page body** (white text) with **white cards**.
  Neither card declared its own text colour, so everything inside them was
  rendered white on white: the pool hours and the three vote choices were
  invisible. The colour is now declared on the surfaces.
- The organizer's name was painted in `var(--bf-appt-dark)` *inside* the header
  block, whose background is that very variable — a 1:1 contrast. It now
  inherits the header's white.
- `bf-btn-accent` sets the text colour and not the background (the parent's own
  templates apply it inline). The submit button was therefore white on the
  white card, and the propose form could not be submitted by sight.
- **"Really reserve the slots" reserved nothing** in *everyone proposes* mode:
  the hold waited for a manual shortlist. It now follows each pick.
- Slots picked beyond `max_picks_per_participant` were **discarded in silence**
  and followed by a green "Your slots are added". What is refused is now
  counted per reason and said. ⚠️ `request.params.get()` returns the *string*
  `"0"`, which is truthy — the success banner used to fire even when nothing
  had been added.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-20, this version converts automatically to
  **LGPL-3.0-or-later**.
