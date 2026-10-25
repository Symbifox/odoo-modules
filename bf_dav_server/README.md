# Calendar and contacts sync (CalDAV, CardDAV)

`bf_dav_server` makes Odoo a CalDAV and CardDAV server. Thunderbird, Apple
Calendar and Contacts (macOS, iOS) and Android through DAVx5 read and write the
Odoo agenda and contacts directly, without Nextcloud or any other relay.

## What a client sees

| Address | What it is |
|---|---|
| `/.well-known/caldav`, `/.well-known/carddav` | Discovery (RFC 6764): redirect to `/dav/` |
| `/dav/principals/<user id>/` | The user, with their calendar and address book homes |
| `/dav/calendars/<user id>/<calendar>/` | One collection per **Calendar for apps** (`bf.dav.calendar`) |
| `/dav/calendars/<user id>/s-<id>/` | A colleague's calendar shared with `bf_calendar_share` |
| `/dav/addressbooks/<user id>/company/` | The company's Odoo contacts (`res.partner`) |
| `/dav/addressbooks/<user id>/personal/` | The user's private book, outside the CRM |

Odoo has one agenda per person; apps expect several named calendars. A
calendar for apps is the *home* of the events created in it. The owner's
default calendar also shows every event they attend whose home is someone
else's, which is what the Odoo agenda shows.

In Odoo's Calendar app, the side panel has a **Calendars** section: one
checkbox per calendar, in the calendar's Odoo color. It always lists every
calendar of the user, even those with no meeting in the period shown;
unchecking one hides its meetings.

## Setting up a device

Preferences → *Account Security* → **New app password** (Odoo asks for the
password first, as for its own API keys). Each device gets its
own password: an Odoo API key of scope `dav`, which opens the calendars and
address books only. It does not open the Odoo login nor the API, and an API
key does not open the calendars. Revoke it from the list of keys in the same
tab.

* Thunderbird, macOS, iOS: the server address (`https://your.odoo/`) and the
  login are enough; the app discovers the rest.
* Android: DAVx5, *Login with URL and user name*, same address.

## What makes the round trip

Create, modify, delete; series (daily, weekly, monthly by date or by weekday,
yearly), moved and cancelled occurrences; attendees and their answers;
privacy (with Apple's `X-CALENDARSERVER-ACCESS`); busy/free; tags; reminders.
Everything an app writes that Odoo does not model is kept and handed back
unchanged: `X-MOZ-*`, `X-APPLE-*`, attachments, the reminders exactly as the
app wrote them (their `UID`, `X-WR-ALARMUID`).

### Reminders and their state

| App | Seen / snoozed / dismissed |
|---|---|
| Odoo (reminder popup, `bf_email_management`) | `bf_dismissed_at`, `bf_snoozed_until` on the attendee |
| Thunderbird | `X-MOZ-LASTACK`, `X-MOZ-SNOOZE-TIME[-<occurrence>]` |
| Apple | `ACKNOWLEDGED` and snooze `VALARM` (RFC 9074) |
| Android | stays on the phone (Android does not sync it) |

The server translates between the three, per person: dismissing a reminder in
Thunderbird dismisses it in Odoo and on the Mac, and the other way round.

Apple's own default reminder (`X-APPLE-DEFAULT-ALARM`) stays on the device that
added it, and a `CLASS:PUBLIC` an app writes on everything does not make public
an event left at its owner's default privacy.

**A write that only changes the state of a reminder changes nothing else**:
the event is not written, its `SEQUENCE` does not move, nobody is notified.
This is what a Nextcloud mirror of reminder state cannot do: Nextcloud sends
"Invitation updated" to every guest on any write.

## Invitations

Odoo is the only sender. The server announces `calendar-auto-schedule`, so
Thunderbird and Apple do not send invitations themselves.

* New guests: Odoo's invitation, as when a guest is added in Odoo.
* Changes made in an app: handed to `bf_calendar_invite`
  (`_bf_announce_remote_change`), which sends its notice only when
  `bf_calendar_invite.auto_change_notice` is on, exactly as for the Nextcloud
  pull.
* Never: for an event organized by someone outside the company (an invitation
  the user received), for guests the app marks `SCHEDULE-AGENT=CLIENT`, when a
  device uploads more than 5 objects in 2 minutes (first sync of a device that
  had local events), or when `bf_dav_server.send_invitations` is `0`.

A write from an app never feeds the Nextcloud mirror (`calendar_nextcloud_sync`,
`contacts_nextcloud_sync`): a pushed PUT cannot be rolled back, and Nextcloud
mails every guest on any write. A calendar used from apps is fed by Odoo only.

Events organized by the system user (synced from Nextcloud) are served as
organized by the owner of the calendar they live in.

## Contacts

The company book serves the Odoo contacts (persons and companies) that are
active, not portal users, and not tagged with one of the book's excluded tags
(Calendar → Configuration → *Address books for apps*, administrators).
Name, company, job title, first email, phone, mobile, first address, website,
tags and photo map to Odoo fields; everything else (more emails and numbers,
birthday, notes, profiles) is kept on the side for that user and handed back to
that user only: a colleague's phone never sees it. Notes written
on a phone never enter Odoo's internal notes. A contact deleted on a phone is
archived in Odoo, never destroyed. Creating a contact needs Odoo's contact
creation right. A picture re-encoded by a phone never replaces a larger one, and
a country is read by name in the installed languages (never as the first
country whose code starts with the same letters).

The personal book stores cards exactly as the app wrote them.

## Conflicts and sync

Strong ETags, `If-Match` / `If-None-Match` honored with 412: a write based on
an outdated copy is refused, never merged in silence. Incremental sync with
`sync-collection` (RFC 6578) and `getctag`, on a change journal kept 90 days;
an older token is refused and the client resynchronizes in full. A refused
request leaves nothing behind (savepoint).

## Limits

* Repeat rules Odoo cannot hold (`BYSETPOS`, several days of the month,
  several months, hourly) are refused with 403 rather than approximated.
* Tasks (`VTODO`) are not served.
* A series "with no end" holds 720 occurrences in Odoo: a device shows more.
* `RECURRENCE-ID` and `EXDATE` are served in UTC: the time zone definitions
  built from pytz stop in 2037, and an occurrence identity must not depend on
  them. The master of a series is its most common shape.
* Other people's private events are shown as busy blocks, as in Odoo.
* Outlook does not speak CalDAV/CardDAV.
* Deleting a meeting from an app does not notify its guests (as in Odoo);
  with `bf_calendar_trash`, the meeting goes to the trash.

## Technical notes

* Reads of a collection are done in bulk, as superuser after checking the
  user's access, with Odoo's masking of private events applied explicitly.
* The module adds an index on `calendar_attendee (event_id, partner_id)`: Odoo
  has none, and listing a 12 000-event calendar otherwise reads the attendee
  table once per event.
* Installation gives existing events, series and contacts a stable UID (the
  Nextcloud UID when there is one) and creates one calendar per Nextcloud
  calendar configuration, so that moving a client from Nextcloud to Odoo shows
  the same objects, not copies.
* Soft links: `bf_email_management` (reminder state), `bf_calendar_share`
  (shared calendars), `bf_calendar_invite` (notices, status, sequence),
  `bf_calendar_hold`, `bf_calendar_block`. None is required.

## Requirements

* Odoo 18.0, with `calendar`, `contacts` and `mail`.
* Python: `vobject`, `lxml`.
* The site must answer on HTTPS at the address given to the apps, without a
  redirect: calendar apps do not follow one while they look for calendars.
  When `web.base.url` is not that address, set `bf_dav_server.public_url`.

## Changelog

* **18.0.1.1.3**: a meeting deleted in an app goes to the trash when
  `bf_calendar_trash` is installed, as when it is deleted in Odoo; an
  *Address books for apps* screen (administrators) to set the tags the company
  book leaves out; labels: the Calendar app's section is "Calendars" in every
  language, the app password window has a title, readable field names; the
  French catalogue is reloaded over the database on update.
* **18.0.1.1.2**: comments and documentation only.
* **18.0.1.1.1**: the Calendars section of the Calendar app lists every calendar
  of the user, even those with no meeting in the period shown; each checkbox
  takes its calendar's color.
* **18.0.1.1.0**: Calendars section in Odoo's Calendar app; each calendar has
  an Odoo color, and an event takes the color of its calendar.
* **18.0.1.0.1**: the app password wizard gives the site's public address.
* **18.0.1.0.0**: first release.

## Licence

Business Source License 1.1, see `LICENSE`. This version becomes available
under LGPL-3.0-or-later on the Change Date the `LICENSE` sets (2030-10-24).
