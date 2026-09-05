# Symbifox Appointment (`bf_appointment`)

Public self-service booking pages, extending *Resource Booking* (OCA).

## Features

- **Public booking pages** per booking type, with slots computed from resource
  availability.
- **Client portal**: self-service confirmation, rescheduling and cancellation.
- **Personal booking links** ("one-time booking"): generate a private link for
  one recipient, with an optional expiry and single-use lock. The link is not
  listed anywhere public. A link that has expired or has already been used says
  so on a proper page instead of silently redirecting.
- **Additional guests, with the requester's confirmation.** The public form can
  offer an "other guests" field. Nothing is sent to those addresses until the
  requester confirms from their own inbox, so the form cannot be used as a
  relay. No contact record is created before that confirmation.
- **Quick link creation** from the email composer (insert into the message, or
  show it below the body ready to copy) and from a contact form. Both buttons
  reopen the composer on the draft you were writing: an action opened with
  `target: "new"` from inside a dialog *replaces* it rather than stacking on
  top, so anything else closes the email you are composing.
- **"Resend invitations"** on a booking sends the branded confirmation again —
  same template, same `.ics`, in each reader's language and time zone. It
  replaces the generic portal *Share* button, which mailed clients "X invited
  you to access the resource booking": the raw model name, no date, no
  attachment. `_bf_resend_invitations()` is the extension point for bookings
  with several recipients, such as an availability poll.
- **SMS reminders.** Each scheduled email can carry a channel — e-mail, SMS,
  or both — with its own short body. The body is screened against the GSM-7
  alphabet and a 150-septet budget when it is written, not when it is sent,
  because a carrier refusal comes back as a bare failure long after the
  author has moved on. E-mail is the fallback on every failure path: no
  number on file, a refused message, or the per-run budget running out. A
  reminder is never silently dropped. Requires an SMS transport module; the
  import is soft, so the channel simply never fires without one.
- **One bad booking no longer takes the run down.** The reminder cron isolates
  each booking: a write that raises — an OCA scheduling constraint, say — is
  caught and logged for that booking alone, instead of escaping the loop and
  stopping reminders for everybody. And a "X hours before" reminder is bounded
  on both sides, so booking inside its own window no longer fires it within the
  minute, and a catch-up run after an outage does not replay "reminder:
  tomorrow" two hours before the meeting.
- **Optional organization field** on the public form, per booking type (off
  by default), stored as the booker's company name.
- **Consent checked on every path that creates a booking**, not only on the
  public intake form (`privacy_consent`). A booking can also be created from a
  personal link, from another module, or by hand in the back office, and none
  of those goes through the intake form. Where there is a page to ask on, the
  question is asked in place: the slot-confirmation dialog carries the consent
  the booking type requires, and carries nothing at all when it is already on
  file. Where there is no page — a poll closing itself, a back-office entry —
  the request goes out by email instead, with its own public reply link.
  Consent and its evidence are written by a single writer, so the record made
  from a personal link is the record made from the public form.
  - A missing consent never blocks the appointment. It blocks the recording.
    A refusal is an answer: it is stored, it is never asked again, and it does
    not cancel the meeting.
  - The consent state of each booking (on file, requested, refused, missing)
    is shown on the record, in the list and in the search filters, so bookings
    that are not covered can be found rather than discovered.
  - **Automatic consent request emails are off by default.** That path writes
    to a client with no human in the loop, on a plain confirmation, so it is
    opened deliberately per database with the `bf_appointment.consent_auto_request`
    system parameter. Asking in place and the consent state do not depend on
    it; neither sends anything.
- **Visitor language.** A booker without an explicit language choice is sent
  once to their own language on the public pages, and the contact record is
  stamped with it at creation, so later correspondence keeps that language. An
  explicit choice always wins over a browser header.
- **Automatic task/project creation** at booking time.
- **Branded emails** with per-company header, brand colours and footer.
- **Portal home tile.** The Bookings tile on the customer portal home carries the
  app's own icon, served at 64 × 64 like its neighbours (v18.0.2.54.2).
- **Onboarding wizard** (`bf_onboarding_base`) to configure booking types.

## Notes on the confirmation links

Booking, cancellation and guest-confirmation links travel by email and by
calendar invitation, so they are clicked with a `GET`. None of those `GET`
requests changes anything: each one renders a page that asks, and only the
resulting `POST` acts. Mail scanners and link previews follow URLs in messages,
so a `GET` that decided would cancel meetings, or send invitations, with
nobody having clicked.

## Dependencies

`resource_booking`, `portal`, `mail`, `project`, `privacy_consent`,
`bf_onboarding_base`, `bf_timezone`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
