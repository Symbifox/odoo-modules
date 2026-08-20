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
  copy to the clipboard) and from a contact form.
- **SMS reminders.** Each scheduled email can carry a channel — e-mail, SMS,
  or both — with its own short body. The body is screened against the GSM-7
  alphabet and a 150-septet budget when it is written, not when it is sent,
  because a carrier refusal comes back as a bare failure long after the
  author has moved on. E-mail is the fallback on every failure path: no
  number on file, a refused message, or the per-run budget running out. A
  reminder is never silently dropped. Requires an SMS transport module; the
  import is soft, so the channel simply never fires without one.
- **Optional organization field** on the public form, per booking type (off
  by default), stored as the booker's company name.
- **Privacy consent** built into the booking flow (`privacy_consent`).
- **Automatic task/project creation** at booking time.
- **Branded emails** with per-company header, brand colours and footer.
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
