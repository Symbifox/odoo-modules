# BF Calendar — usable invitations (`bf_calendar_invite`)

Makes the **EMAIL** and **SMS** buttons on a calendar event produce something a
recipient can act on.

## What core does, and where it stops

The EMAIL button (`action_open_composer`) loads a template that describes the
meeting but carries **no `.ics` attachment and no link to the event**. The
recipient reads the details and has nothing to click and nothing to add to
their calendar. The `.ics` is only ever attached by "Send Invitations", a
button core keeps in the Invitations tab behind the developer group.

The SMS button opens the composer with an empty body.

## What this adds

- **`invitation.ics` on the EMAIL button.** Attached through the template's
  dynamic report, so it survives the composer's recompute and is regenerated at
  sending time. Same file core produces for its own invitations.
- **A link to the invitation page**, where the attendee can accept or decline.
  Only when the event has exactly one attendee besides the organiser — the
  token in that URL *is* the attendee's identity, so one link cannot be handed
  to a group.
- **A branded message**, in the company's colours and logo (`res.company`,
  fields from `bf_onboarding_base`), rather than a bare block of text. The dark
  header uses `report_brand_logo` where it is set, since the standard company
  logo is the one drawn for light backgrounds.
- **Written in the guests' language.** "Send Invitations" renders one message
  per attendee and can follow each of them; the EMAIL button renders one
  message for the whole list and has to choose. It takes the outside guests'
  language when they share one, and the organiser's when they do not.
- **A prefilled SMS**: title, date and time, and the same link. Kept inside the
  GSM-7 alphabet and under 160 characters so a reminder stays one segment.

- **A meeting status** (`Tentative` / `Confirmed` / `Cancelled`), distinct
  from "Attending?". The status says whether the meeting itself is going
  ahead; "Attending?" is one guest's answer to the invitation, so a confirmed
  meeting can have guests who declined and a cancelled one can have guests who
  had accepted. It is written into the `.ics` `STATUS` property and read back
  from it, so it survives a round trip through a calendar client. Only the
  three values RFC 5545 §3.8.1.11 defines for a VEVENT exist: a status a
  client invented is not silently promoted to "confirmed". Shown on a popover
  in the calendar view.
- **A POKE button** — a short "are we still meeting?" note to the guests, in
  their own language. No `.ics` is attached: the event has not changed, and
  re-attaching one reads as a reschedule. The message repeats where to join
  instead, because the commonest reason someone is missing is that they cannot
  find the link.

Both remain drafts: the composer opens, the user edits and sends.

## Changing the language of one message

The body is rendered once, when the template is picked, so the language cannot
be changed by typing in the composer. Three templates are shipped, with a
single body behind all three: the default one, which follows the guests, and
two that force French or English. Switching means picking another one from the
composer's template dropdown.

The two forced templates ask `res.lang` which variant the database actually has
(`fr_CA`, `en_CA`, …) instead of naming one. A deactivated language only half
works: the prose comes out right, because an untranslated term falls back to
the English source, while `format_datetime` resolves its locale among the
*installed* languages and drops back to the first one. The message then reads
"Here are the details" above "jeudi 10 septembre".

## Notes

- The `.ics` carries no `METHOD:REQUEST`, matching what core sends. Mail clients
  offer "add to calendar" rather than treating it as an RSVP invitation, which
  keeps Odoo's own accept/decline links authoritative.
- **Hours carry the company's timezone, not the sender's.** Core's
  `_get_mail_tz()` ends at `env.user.tz`, so an organiser writing from New
  Zealand announces 7 a.m. to a client in Montreal. The chain is `event_tz`
  (recurrences only), then the company's working hours, then the company
  contact, then the sender.
- **The templates are rewritten by every module update.** They are ordinary
  records with no `noupdate`, which is deliberate: the source is `data/`, not
  the database. Rewording them under Settings → Technical → Email Templates is
  good for one message, not for keeping an edit — for that, change
  `data/mail_body.xml`.

## Dependencies

`calendar`, `calendar_sms`, `bf_onboarding_base`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
