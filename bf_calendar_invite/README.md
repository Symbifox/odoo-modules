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
  in the calendar view, and — from **v18.0.3.2.0** — on the tile itself: a
  tentative meeting is hatched at 45°, the texture core already uses for
  `o_event_hatched`. Not a paler fill or a lower opacity: core already dims
  every meeting awaiting an answer, which on a real calendar is nearly all of
  them, so a paler tentative would have been invisible. The hatching is drawn
  in `currentColor` — the tile's own text colour, which core has already
  contrasted against each of its 56 palette entries — so it reads on all of
  them, in light and dark alike, without restating a single value. A meeting
  with no status set stays unmarked: the field was deliberately never
  backfilled, and painting absence as "confirmed" would assert a confirmation
  nobody gave.
- **A clickable location.** Where a meeting's location is a room URL — which is
  most of them, once one is filled in — following it took selecting the text by
  hand. The field stays a free `Char`; only the part actually recognised as a
  URL becomes a link, the text around it stays text, and a value with no URL
  renders exactly as before. Core's `url` widget was not the one-line answer it
  looks like: it turns the *whole* value into a link and prefixes anything
  without a scheme, so a street address would have become a dead hyperlink —
  worse than no link at all, because it offers itself and leads nowhere.
- **A POKE button** — a short "are we still meeting?" note to the guests, in
  their own language. No `.ics` is attached: the event has not changed, and
  re-attaching one reads as a reschedule. The message repeats where to join
  instead, because the commonest reason someone is missing is that they cannot
  find the link.

Both remain drafts: the composer opens, the user edits and sends.

## The `.ics` identity — what makes an update an update

Odoo's "date updated" mail already went out on every reschedule. What it carried
updated nothing.

`calendar.event._get_ics_file()` builds the calendar with `vobject.iCalendar()`
and **sets no UID**. vobject therefore invents one at every serialization, out
of a timestamp, a random number and the container hostname — two serializations
of the same event fifty minutes apart carry two different UIDs. A calendar
client receiving a `METHOD:REQUEST` under an unknown UID **adds an entry**; it
cannot move the one it holds. That is why a meeting moved in Odoo only ever
reached a guest's calendar through the remote calendar's own iMIP plugin: the
identity of the meeting lived there, not in Odoo.

This module therefore puts three things on the event:

| Field | Role |
|---|---|
| `bf_ics_uid` | the RFC 5545 identity, reused by every later `.ics` |
| `bf_ics_sequence` | the revision (`SEQUENCE`, RFC 5545 §3.8.7.4) |
| `bf_ics_recurrence_id` | for an occurrence pulled out of its series, the slot it used to hold |

⚠️ **`x_nc_uid` wins where it exists.** That field belongs to
`calendar_nextcloud_sync` and holds the UID the remote calendar already knows;
minting a second one beside it would give one meeting two identities. The link
is **soft** (`in self._fields`) — this module also runs on tenants with no
calendar sync at all.

⚠️ **The revision only moves on a material change** — start, stop, title,
location. Not on the description: a typo fixed in the notes is not a reschedule,
and bumping `SEQUENCE` for it would train clients to re-ask guests for nothing.
The comparison is made on **normalised** values: the web client posts `start` as
a string while the record holds a `datetime`, and a raw `!=` would find them
different at every save.

### Recurrence

Core copies the `RRULE` onto **every** occurrence, so the `.ics` for a single
moved Thursday describes a whole weekly series. Three shapes here:

- a plain meeting: its own UID, no `RECURRENCE-ID`, no `RRULE`;
- the **base** event of a series: the series UID and the `RRULE`;
- **any other occurrence**: the series UID plus a `RECURRENCE-ID` naming the
  slot, and no `RRULE`.

⚠️ The `RECURRENCE-ID` is the **original** slot, not the new one — it answers
"which occurrence moved". It is captured **before** the write: once `start` is
overwritten, the slot the occurrence used to hold is gone. An occurrence
detached before the field existed therefore has no anchor, and falls back to an
identity of its own. Standing alone is wrong in the small; rewriting the series
is wrong in the large.

### Organiser and description

`ORGANIZER` is built from a **parsed** address. `res.partner.email` is not
guaranteed to hold a bare address, and `"mailto:" + email` then produces a URI
that is invalid under RFC 6068 — a client that rejects the URI rejects the whole
VEVENT. The field is not repaired: its value may well be wanted as it stands,
and an ICS generator is not the place to rule on that.

The description loses its `text/html,…` wrapper. Events pulled in over CalDAV
can carry a description shaped as `text/html,<percent-encoded html>":<the same
text, in plain>` — a `data:` URI that lost its scheme on the way in. The plain
half is taken as it stands; it comes from the producer, not from us. ⚠️ Only the
**outgoing copy** is repaired: the field itself keeps its value, and the next
event ingested arrives the same way.

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
