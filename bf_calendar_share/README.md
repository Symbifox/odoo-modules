# BF Calendar Sharing

Choose what each colleague sees of your calendar, and share it with people
outside the company through read-only links, with open standards only.

## Why

Odoo's calendar knows two levels, set by the author of each meeting: everything
(except private meetings) or "Busy". Nothing can be shared outside the company.
Popular calendars offer more: Outlook has availability only, titles and
locations, all details, edit; Google has availability only, all details, edit.

## Inside the company (four levels)

⚠️ A display setting, not a security boundary (decided 2026-10-14): it hides
fields in the calendar, forms, names, attendees and the chatter, like Odoo does
for private meetings, but a colleague can still find out whether a meeting with
a given title exists by searching for it, and SQL-based views such as the
meeting dashboard of `bf_meeting` show real titles. Other known limits, the
same as for Odoo's own private meetings: a "Meeting" activity planned on another
record shows the meeting's location and attendees in the activity panel, and
server-side jobs (scheduled actions, scripts) are not masked at all, so that
reminders keep working. Use *Private* for what must stay secret.

Hidden at the restricted levels: title (below *Titles and locations*),
description, attendees, tags, video call link, the chatter's messages,
attachments and followers, and the right to change or delete the meeting
(except the meeting dashboard's own "dismiss" switches).

- *Calendar > Sharing > Share my calendar*: one line per colleague, with a
  level: **Availability only**, **Titles and locations**, **All details**, **Edit**.
- *Preferences > Colleagues see my calendar*: the level for everyone else.
  The default is **All details**, Odoo's usual behaviour: nothing changes at
  installation.
- People invited to a meeting always see it. A private meeting always shows as
  *Busy*, at every level.
- Below *All details*, a colleague cannot change or delete a meeting they cannot
  see. *Edit* lets them change your meetings as if they were invited.
- Technical accounts (meeting processing, integrations) go in the group
  *Calendar: see every calendar in full*, which ignores the levels (private
  meetings stay hidden).

## Outside the company (links)

*Calendar > Sharing > Links to my calendar*: one link per person, with a level
(**Availability only**, **Titles and locations**, **All details**) and three
addresses to send:

- **Subscription** (iCalendar, RFC 5545): Outlook, Outlook.com, Microsoft 365,
  Google Calendar, Apple Calendar and Thunderbird subscribe to it, read only.
  They refresh it on their own schedule (several hours for Outlook).
- **Availability** (VFREEBUSY): busy times only, for clients that look up
  free/busy information.
- **Web page**: the next four weeks, for people without a calendar application.

What a link never shows, at any level: attendees (third parties), video call
links (they give access to the meeting) and tags (only the work block type of
`bf_calendar_block`). Private meetings show as *Busy*, including when the
organizer's default privacy is private. Meetings marked *Only internal users*
are shown at the link's level: creating the link is the decision to show them
to that person.

Links expire after 90 days by default (one reminder activity seven days
before), can be extended, revoked, or given a new address. Each opening is
logged with its full IP address and client, for traceability, kept one year by
default (system parameter `bf_calendar_share.access_retention_days`); the page
does not say so. Administrators can read every link and its log (inventory of
what is shared outside). Meetings organized by someone else follow the privacy
their organizer gave them: only public ones are shown, the others are *Busy*.
Declined invitations are left out. The filtering is done on the server: a link
at *Availability only* does not contain any title.

With `calendar_nextcloud_sync`, `bf_calendar_share_nextcloud` lets each link
choose the Nextcloud calendars it shows.

## Outlook, both ways

No open protocol lets Outlook edit a calendar that is not hosted by Microsoft
(Outlook does not speak CalDAV, and Microsoft Graph is not an open protocol).
Both ways, with open standards: Outlook subscribes to the link above, and Odoo
subscribes to the calendar Outlook publishes (`bf_calendar_subscribe`). A
single meeting goes both ways through invitations (iMIP, `bf_calendar_invite`).

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.1.4 | 2026-10-15 | *Full history* on a link: every past meeting with no limit, and the next ten years |
| 18.0.1.1.3 | 2026-10-15 | Web page of a link: the doctype was escaped and showed as text at the top of the page; the example in the link name is now generic |
| 18.0.1.1.2 | 2026-10-15 | French: the link form said the access log is kept 90 days; it is kept one year by default (`bf_calendar_share.access_retention_days`) |
| 18.0.1.1.1 | 2026-10-15 | System parameter `bf_calendar_share.base_url`: the public address used in links, when it differs from `web.base.url` (a `www.` address that redirects with a 308 is followed badly by some calendar clients) |
| 18.0.1.1.0 | 2026-10-14 | Links outside the company (lot 5): iCalendar subscription, free/busy and web page, filtered by level on the server, expiry, reminders, access log |
| 18.0.1.0.0 | 2026-10-14 | Sharing with colleagues by level (lot 4) |

## License

LGPL-3.0
