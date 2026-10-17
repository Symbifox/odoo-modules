# Symbifox Appointment (`bf_appointment`)

Public self-service booking pages, extending *Resource Booking* (OCA).

## Features

- **Public booking pages** per booking type, with slots computed from resource
  availability.
- **Client portal**: self-service confirmation, rescheduling and cancellation.
- **Cancelling keeps the calendar entry.** A cancelled booking used to have its
  `calendar.event` deleted, and the reason was real: an event left behind kept
  blocking the slot, so the same resource combination answered "no availability"
  on a slot nobody was using. `show_as` settles that on its own — the slot
  generator counts an event as busy only when it is marked busy — so the event
  now stays where it is, struck through and marked Available. Measured on a real
  calendar before the change: *every* cancelled booking had lost its event,
  without exception, so the agenda kept no trace of a slot that had been held
  and then fell through.

  Cancelling from the backend also offers the **cancellation notice**. The
  branded message has always existed, but only the public confirmation page ever
  sent it: a booking cancelled from Odoo told nobody, and the person who did not
  show up was the one who never heard. The dialog asks first, unticked by
  default, and names the address that would be written to — a booking is
  cancelled both because a client phoned to move it (tell them) and because it
  was a duplicate or a test (do not). The organiser's own copy is a separate
  tick, off by default, since from the backend the organiser is usually the one
  cancelling.
- **Personal booking links** ("one-time booking"): generate a private link for
  one recipient, with an optional expiry and single-use lock. The link is not
  listed anywhere public. A link that has expired or has already been used says
  so on a proper page instead of silently redirecting. **A recipient who books
  through the public page instead of following their link resumes that link**
  rather than opening a second booking beside it: the title the organiser wrote
  stays, and no orphan link is left behind to produce a second meeting. This
  matters because a personal link never travels alone — the same email usually
  carries a generic booking link in the signature.
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
- **Visitor time zone.** Slot times follow the browser's own zone, then the
  booking type's display window, then the company calendar — the contact record
  is read last, because that field is rarely filled in by the person it
  describes. The detected zone is stored on the booking, so the picker, the
  confirmation page, the emails and the `.ics` all state the same hour.
- **Visitor language.** A booker without an explicit language choice is sent
  once to their own language on the public pages, and the contact record is
  stamped with it at creation, so later correspondence keeps that language. An
  explicit choice always wins over a browser header.
- **Automatic task/project creation** at booking time.
- **Branded emails** with per-company header, brand colours and footer.
- **Portal home tile.** The Bookings tile on the customer portal home carries the
  app's own icon, served at 64 × 64 like its neighbours (v18.0.2.54.2).
- **Onboarding wizard** (`bf_onboarding_base`) to configure booking types.
- **Several people on one slot.** A booking type can declare how many bookings a
  single slot accepts (`slot_capacity`, v18.0.2.59.0). At the default of 1 the
  availability engine behaves exactly as before; above it, the slot stays on
  offer until the cap is reached, which is what an open house or a group intake
  needs. Taking a slot on such a type locks the type row for the duration of the
  write, so two visitors reading "two of three taken" at the same moment cannot
  both get in.
- **Satellite surface.** `_bf_candidate_slots()` accepts a `combination`
  argument (v18.0.2.59.0), so a companion module that publishes one page per
  bookable object answers with that object's availability rather than the union
  of every combination on the type.
- **Extra links, placed by a satellite.** `bf_extra_links()` returns a list of
  `{label, url, help}` and is empty here (v18.0.2.60.0); a companion module
  overrides it, and all four surfaces the booker sees render the result — the
  confirmation email and the four reminders, the public booking page, the
  description of the `.ics`, and the calendar event's own description. The
  method lives in this module rather than in the satellite on purpose: the mail
  templates call it from their `body_html`, so on a tenant without a satellite
  a missing method would raise while rendering and the booker would receive no
  confirmation at all. An empty fallback is the only safe shape. Both entry
  points are public (no leading underscore) because the QWeb of a `mail.template`
  is sandboxed and refuses to call a private method — it renders
  `'NoneType' object is not callable` rather than an error you can read.
- **"Reschedule this appointment" link** (v18.0.2.61.0). The module now fills
  `bf_extra_links()` itself with a link to the booking's own slot page, so the
  booker moves the appointment instead of cancelling and booking again. The link
  is offered only when rescheduling actually works (`bf_can_reschedule()`): the
  booking is not cancelled, its time is still ahead, the type's modification lock
  (`modification_lock_hours`) has not passed, and the link still opens the slot
  picker. It withdraws itself when the lock falls, with no setting to maintain. A
  missing access token is generated before the URL is written, so the link never
  leads to a 404.
- **A move is not announced as a new booking** (v18.0.2.61.0). When the booker
  picks a new time from their personal link, they receive the before/after change
  notice of `bf_calendar_invite` (soft dependency; without it, the usual
  confirmation is sent as before), and the organizer receives a dedicated
  **"Rescheduled"** email showing the previous time, instead of "New booking" for
  a meeting that already existed.
- **Staff can move a booking from the calendar or from the booking form**
  (v18.0.2.62.0), and both behave the same. Moving a booking outside the hours
  offered to clients used to fail with "Cannot schedule these bookings because
  no resources are selected for them": automatic assignment found no free
  resource at the new time, cleared the one in place, and the check then
  refused a booking with no resource. The resource now stays in place. An
  appointment manager (`resource_booking.group_manager`) can place the meeting
  anywhere; anyone else is refused with the real reason, in their language.
  The override applies to moves only: creating a booking outside the offered
  hours is still refused, even for a manager, and the public booking pages stay
  strict.
- **"Appointment" button on the calendar event.** A calendar event that holds a
  booking opens it from its smart-button area, cancelled bookings included.
- **One H1 on the public booking page** (v18.0.2.62.2): "Book a meeting" is the
  page heading, kept at its previous size.
- **Bounded rate limiter on the public forms** (v18.0.2.64.0). Past 10,000
  tracked addresses the limiter used to clear every bucket, which let a source
  erase its own block by cycling addresses. It now drops expired keys first, then
  the lightest ones; a blocked key goes last.
- **Emails go through the shared mail layout** (v18.0.2.64.0), see below.

## Emails and the shared mail layout

The fifteen booking emails (acknowledgement, confirmation, reminders, follow-ups,
cancellation, organizer notices, guest emails) carry only their content: the shared
mail layout (`bf_onboarding_base.bf_mail_layout`, which `bluefox_branding` replaces
with its own when installed) dresses them with the logo, colours and footer of the
booking type's company. A booking and a guest have no `company_id`, so
`_mail_get_companies` names the booking type's company. Accents in the content, and
the extra-link buttons, use the company brand colours (`report_brand_*`), like the
layout. The contact sentence reads `res.company.bf_appointment_contact()`, which
skips the placeholder defaults (`service@example.com`, `555-555-5555`) for the
company's own email and phone, and hides the sentence when there is nothing to cite.
Footer notes that are not branding ("Notification interne", "Invitation transmise
via…") stay at the end of the content.

⚠️ The privacy and terms links now come from the layout footer, that is from the
company's brand settings (`brand_privacy_url`, `brand_terms_url` in
`bluefox_branding`). The appointment-specific URLs are no longer read by the
emails: copy them there if your company relied on them.

The templates are `noupdate`: migration 18.0.2.64.0 strips every stored language
with the same tool as the source, all or nothing per template, and leaves a body
rebuilt by hand as it is. "Rendez-vous : Rappel (ancien)" is unused and left
untouched.

## Meetings open without the Bookings group (18.0.2.64.2)

The OCA `resource_booking` module puts `resource_booking_ids` on the meeting form
without restricting it to a group. The web client reads every field of a view,
invisible ones included, so an internal user without the Bookings group got an
access error when opening any saved meeting. The field is now restricted to that
group (`views/calendar_event_booking_access.xml`).

## Meeting title, token pages and guests (18.0.2.65.0)

Every booking email names the meeting by its full title, the one the organizer's
calendar shows and the attached .ics carries: `resource.booking.bf_title()` (the
booking name, else the calendar event name, else the generated "Type - Organisation
x Brand"). It is the subject of the sixteen templates and the "Rencontre" row of
their body, in place of the booking type name ("Rencontre flexible"), which said
neither which meeting it was nor with whom. The .ics summary and description use
the same title.

The pages reached by the booking token (pick-a-time, confirmation, cancel, guests,
their page title, which a link preview shows when a booking link is pasted, and the
portal breadcrumb above them) and the Nextcloud Talk room name use
`bf_page_title()` instead. The public form
finds the contact by the email address typed, with no proof that the visitor owns
it, and sends the browser straight to the token pages: the full title, built on
the organisation of that contact, would show it to anyone who knows an address. A
booking made on the public form therefore stores `bf_public_title`, built on what
the visitor typed only (the typed company when the type collects it, else the
typed name), and those pages show it. The calendar, the emails (sent to the
address) and the .ics keep the full title. Personal links and bookings made by the
team have no public title: their pages show the full title. Bookings made before
2.65 without a source (the public form, but also bookings made by the team in the
backend) get the booking type name, as their pages showed it. Token
pages answer with `X-Robots-Tag: noindex, nofollow`; the landing and booking type
pages stay indexable. Both methods are `api.private`: templates call them, RPC
cannot.

The templates are `noupdate`: migration 18.0.2.65.0 rewrites every stored language,
replacing only the type-name expression and the label of the row that shows it, so
a subject or body customised on a tenant keeps its wording. A body's language is
read from its content, not its key ("Meeting" when it holds no French). Templates
created by hand, without an xmlid of this module, are left untouched.

The guest invitation goes to an address typed by the booker: it carries
`bf_page_title()` too.

Guests are confirmed with a key, not with the booking token alone. The public form
hands the token to the browser on the typed address only, so up to 2.64.2 anyone
could type someone else's address, add guests and send them invitations. The
confirmation request, sent to the booker's address, now carries
`bf_guest_key()` (an HMAC of the booking id with the database secret, nothing
stored), and `/guests/confirm` and `/guests/decline` check it in constant time on
GET and POST. Without it nothing changes and a neutral page says nothing about the
booking. Migration 18.0.2.65.0 adds the key to both links of the stored template
(left as is, with a warning, when it was customised) and marks the pending guests
whose request was already sent (`bf_confirm_by_token`): their old link keeps
working until the meeting ends, and no guest created later is ever marked.

The booking token also opens the `resource_booking` portal
(`/my/bookings/<id>?access_token=…`), which has none of the guards of the token
pages: it shows the full title and the participants as they are in the address
book, and its `/confirm` and `/cancel` act on a plain GET, without the link expiry
check, the on-screen consents or this module's emails. When the token is the only
thing that opens the booking to the visitor, those four routes (page, schedule,
confirm, cancel) now redirect to the matching page of this module and do nothing
else. A signed-in user who can open the booking by their own rights keeps the
portal, links with a token included.

The token no longer opens the booking's message thread either: Odoo's portal
chatter routes (`/portal/chatter_init`, `/mail/chatter_fetch`, `/mail/message/post`,
reactions, attachment upload, message avatars) accept the record token, or the
`hash`/`pid` signature, and showed the name and avatar of the contact found by the
typed address, or posted under it. `resource.booking._get_thread_with_access`
ignores both: only the visitor's own access rights open the thread. A signed-in
user with access keeps the chatter; the token pages of this module show none.

No brand is built in any more: the `appointment_brand_*` fields of the company have
no Blue Fox default (name, logo, website, privacy and terms links, tagline), and
the .ics organizer is the company name. A company that sets nothing sends under
its own name. Values already stored on a database are not changed. The booking
type description takes the site's text color instead of a forced white, which was
invisible on a light site, and the portal breadcrumb says "Rendez-vous" in French.

## Privacy: purpose, retention and anonymization (18.0.2.66.0)

The module already depended on `privacy_consent` for the two consents of the
public form (recording, newsletter). The booking itself was declared nowhere.

- **Purpose** "Prise de rendez-vous" (`appointment_booking`) in the record of
  processing, with a plain-language summary. No consent is asked: organizing the
  meeting the person just requested is the reason for the collection.
- **Retention rule RDV-001**: 3 years after the end of the meeting, then
  anonymization. Intake answers and guests follow the same deadline. A request
  never placed on the calendar counts from its creation. The duration is **read
  on the rule**: changing RDV-001 changes the cron, archiving it stops it, and a
  zero duration is refused.
- **Anonymization**, applied every night by the cron "Rendez-vous :
  anonymisation des réservations échues (RDV-001)", one booking at a time, each
  committed on its own:
  - what satellites copied is removed first, through the hook
    `_bf_privacy_anonymize_extra()` (the agenda's seeded topic and requester in
    `bf_appointment_meeting`, the originating poll in `bf_appointment_poll`);
  - intake answers and guests are deleted, with the emails sent to guests;
  - attendees are replaced by the contact "Personne anonymisée" (the field is
    required by the OCA), which takes over the requester's response, so a
    confirmed booking stays confirmed;
  - title, description, cancellation reason, video link, tokens and source are
    cleared, and the location goes back to the type's (an in-person booking
    without a fixed place carried the address the requester typed);
  - the chatter of the booking and of its calendar event (emails, notes,
    tracking), their attachments, ratings, activities, followers and email
    tracking are deleted;
  - the calendar event loses its external attendees, description, locations and
    the "before" values kept by `bf_calendar_invite`;
  - the Nextcloud or Google copy of the event receives the anonymized version
    (only a copy already pushed: nothing is created remotely), and the booking's
    own Nextcloud Talk room is renamed (never the shared fallback room);
  - classifications of the booking are archived and lose the old title.
  Type, organizer, resources, date, duration and state remain for statistics.
  Writes run without tracking, notification or `bf_calendar_invite` baseline, so
  the old name is not written back anywhere and no invitation goes out. Outbound
  calls come after the database writes and raise on failure: the booking is then
  left intact and retried the next night.
  Each anonymization writes a **partial** entry in the destruction register
  (method "anonymize", the removed fields named in `res_field`), with no trace of
  the old name, and the list of what is not covered.
- **Not covered**, and said in each register entry: the contact records of the
  requester, confirmed guests and poll participants, and their consents (own
  regimes; a consent names the booking number); the meeting record and agenda
  (rule RENC-001), except what satellites remove; linked visits and outreach
  touches; the Talk conversation, its messages and guests (the room is only
  renamed); the video short link; event tags; Nextcloud and Google trash and
  history; backups until they rotate; copies of emails in mailboxes; SMS logs at
  the provider.
- A **destruction campaign** or **destruction request** cannot anonymize a
  booking: the generic method archives it without removing anything and the
  register would say "anonymization", so the campaign refuses and the request
  lists the booking as not destroyed, with the reason. "Delete" goes to the base
  as usual.
- `bf_privacy_anonymized_at` is set only by the anonymization, which runs as
  superuser (refused by hand, by RPC even with a forged context, and at
  creation). The contact "Personne anonymisée" cannot be deleted, archived or
  merged.
- `resource.booking` is **classifiable** (`_privacy_classifiable_models()`). Its
  answers and guests are not: they cascade from the booking, and a
  classification on the booking covers them.
- **Personal-information category per intake question**, chosen when the
  question is configured. "Email" and "Phone" fields are pre-filled with
  "Identification"; every other question stays empty until someone chooses,
  since a label is not enough to guess. The booking type shows how many
  questions have no category, and how many collect sensitive information.

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
- **Change Date**: on 2030-10-17, this version converts automatically to
  **LGPL-3.0-or-later**.
