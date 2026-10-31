# Symbifox Appointment — Google Meet (`bf_appointment_google_meet`)

Adds **Google Meet** to the "Videoconference provider" choices of appointment
types (`bf_appointment`).

## How it works

- When an appointment is confirmed, its calendar event is pushed to the host's
  Google Calendar (`calendar_nextcloud_sync`). This module asks Google to add a
  Meet to **that** event, then writes the link on the appointment. The
  confirmation email and the calendar invitation carry it.
- The client is a guest of the Google event. Signed in to Google with the
  invited address, they join without asking to be let in; otherwise (no Google
  account, guest added on the Odoo side only) the host admits them. Google sends
  no email (`sendUpdates="none"`): Odoo sends the confirmation, as before.
- A move keeps the same Meet link, including during a temporary Google outage
  (network, server error, expired token): the reason is noted on the
  appointment and the link the client received does not change.
- Cancelling deletes the Google event. That does not guarantee the Meet code
  stops working.

⚠️ Known limitation of `calendar_nextcloud_sync`: once its inbound pass has read
the event back from Google, the event is marked as coming from Google, and moves
made in Odoo are no longer pushed to the host's Google Calendar. The Meet link
stays valid, but the time shown in Google can stay the old one.

## Fallback to Nextcloud Talk

When the Meet cannot be created, the appointment gets a Nextcloud Talk room,
exactly as a Talk type would, and the reason is noted on the appointment for the
host. Cases covered:

- the host has no synced Google Calendar (configuration missing, archived or
  one-way);
- the event is on someone else's Google Calendar than the host's;
- the event has not reached Google yet (push refused);
- Google refuses, or does not return a link in time.

An appointment already on Talk stays on Talk when it is moved: the link the
client received does not change. The reverse is not guaranteed: an appointment
on Meet moves to Talk when the Meet is gone for good (event deleted in Google,
change of host, archived or one-way configuration), and the client gets the new
link in the change notice. When a conference was requested but not obtained, the
module withdraws the request (best effort) so the Google event does not offer a
second room.

Notes on the appointment are written in the host's language.

⚠️ The fallback needs Nextcloud Talk configured in the `bf_appointment`
settings (address, service account, app password).

## Edge cases

- A host with several calendar configurations (Nextcloud and Google) can be
  routed to the Nextcloud one: their Meet appointments then fall back to Talk.
- With several people assigned (K-of-N), the organizer stays the booking's own,
  and the Meet is created on their calendar.
- If the event already carried another conference solution (Zoom, for example),
  the Meet request replaces it.
- A type switched from Google Meet to Talk: on the next move, the Talk path does
  not recognise the Meet link and creates a room; the link changes.

## Dependencies

`bf_appointment`, `calendar_nextcloud_sync`. Installed explicitly, tenant by
tenant: nothing changes until a type is set to Google Meet.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
