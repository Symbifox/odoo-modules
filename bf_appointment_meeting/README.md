# Symbifox Appointment Agenda

The bridge between the booking engine and meeting agendas: when someone books
an appointment, the agenda for that meeting is created on the spot, and the
booker is invited to say what they want to discuss while the question is still
fresh in their mind.

## What it does

- **One checkbox per booking type**, unchecked by default. A type that carries
  it builds an agenda as soon as a booking is confirmed — from the public page,
  from a personal link, or typed in the back office: all four creation paths go
  through `action_confirm`.
- **The project**, which `meeting.agenda` requires, comes from the booking type.
  When the type names none, a company-level fallback project takes over. With
  neither, **nothing is created**, and a note on the booking says why: an agenda
  filed at random costs more than an agenda that is missing.
- **A contribution link travels with the confirmation**, on all four surfaces
  the booker sees — the confirmation email, the reminder emails, the public
  booking page, and the `.ics` invitation. The agenda has just been created and
  is empty, so the link is not there to *read* an agenda: it is there to ask
  "what would you like to discuss?".
- **The intake answer becomes the first topic**, when there is one. It is what
  the booker has already written; asking again would be rude. That topic is
  created **accepted** — it came from our own form, not from the public page,
  where proposals land in moderation.
- **Cancelling closes the window**, and cancels the agenda if it is still empty.
  As soon as it carries a contribution, objectives, context or preparation, it
  survives and only the contribution window shuts, with a note saying why. A
  cancelled meeting often has a sequel.
- **Rescheduling moves the agenda**, date and planned duration together. An
  agenda that still shows the original slot contradicts the invitation sitting
  next to it.

## What it deliberately does not do

It sends **no agenda email**. `sent_date` stays empty and the organiser keeps
control of what goes out and when. The link that rides along with the booking
confirmation is a contribution link, not a publication of the agenda.

It does not run an assistant pass on the new agenda either. `bf_meeting` offers
one on creation; here the agenda's public link leaves within the same second,
and machine-written text nobody has reviewed should not be the first thing a
client reads from you. The organiser triggers that pass when they open the
agenda — that is, when they can read it before anyone else does.

## Configuration

| Where | Setting |
|---|---|
| Booking type | **Create an agenda** — off by default |
| Booking type | **Project** — where the agenda is filed (already part of `bf_appointment`) |
| Settings → Appointments | **Fallback project for appointment agendas** |

A type with the checkbox and no project anywhere produces no agenda. That is
the safe state, and it is visible: the note on the booking names the type.

## Requirements

`bf_appointment` (18.0.2.60.0 or later, for the `bf_extra_links()` surface the
four link renderings go through) and `bf_meeting` (18.0.3.58.0 or later, for
`contributions_preopened`, which opens the contribution window without waiting
for an agenda email).

## Licence

Business Source License 1.1 — see `LICENSE`. Each version converts to
LGPL-3.0-or-later four years after its release.
