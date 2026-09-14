# Symbifox Absence Autoreply (`bf_contact_absence_autoreply`)

Declare yourself away **once**, and the responder, the calendar and the return
all follow.

Depends on `bf_contact_absence` (the status) and `bf_email_management` (the
responder). It adds no model of its own to either: it connects two things that
already existed and had never spoken to each other.

## Why this module exists

The out-of-office responder shipped complete: RFC 3834 kept rule by rule, a
different message per audience, a stand-in who actually receives the thread,
invitations declined for the period, a log of every send and every refusal.

Weeks later, on every database where it ran: **zero records. Zero replies ever
sent.**

That is not a quality problem, it is an **arming** problem. Three things had to
happen before a period could ever arm itself: tick a setting in your profile,
have **already written** a template, and name your calendar event with one of
the words a pattern looks for. Three prerequisites, two of them invisible.

And the third does not hold anyway. **People name a trip after where they are
going**, not after the word "holiday". A regular-expression net cast over event
titles will never see a trip called by its destination.

## What the module does

- **The status commands.** An absence noted on the record of someone in your
  organisation arms their responder for the period, moves it when the dates
  move, and switches it off when you untick it, archive it or delete it.
- **"I'm away", one gesture.** Dates, stand-in, tone of the message, and
  whether invitations landing in the period are declined. Reachable from
  Contacts and from the mailbox.
- **It shows the text before it arms.** An automatic reply is the one thing you
  write and never read: you do not receive your own outgoing mail. The wizard
  renders the message, stand-in sentence included, before anything is armed.
- **Two default messages**, seeded at installation and meant to be rewritten:
  one that announces a **delay** ("I am travelling until X, my replies will be
  slower") and one that announces an **absence**. A personal template still
  wins when there is one.

## The rules that hold the rest together

1. **No text, no answer.** Neither a personal template nor a house message
   means nothing arms, and the screen says so. An empty template sent to a
   client costs more than a silence.
2. **The absent person's timezone decides.** The status carries dates, the
   responder carries instants, and between two distant timezones there is more
   than half a day. Read in the wrong one, "away from the 16th to the 18th"
   arms or disarms almost a day off.
3. **No product text agrees in gender.** Odoo does not carry a person's gender
   and guessing it from a first name gets it wrong; a house message goes out
   under anybody's name. A test checks the shipped texts for it.
4. **Noting an absence and arming a responder are not the same permission.**
   Anyone may note that a colleague is away, and that is deliberate. But arming
   a responder means writing to clients **under their name, from their
   mailbox**: that is the holder's to do, or an email administrator's.
5. **Nothing arms for a contact.** A client's absence stays what it is, useful
   knowledge at the moment you write to them, not a commitment that answers on
   their behalf.

## What it fixes on the way

Two defects in the calendar detection, measured on a live calendar and fixed in
`bf_email_management` 18.0.11.35.0:

- the detection read the event's **organiser**, while on a calendar kept by a
  synchronisation two thirds of the events belong to the superuser. It now
  reads the organiser **or the attendees**, and a **declined** invitation arms
  nothing;
- an **occurrence of a recurrence** no longer arms anything: an annual public
  holiday would otherwise have armed a responder every year for centuries.

## Models

| Model | What it holds |
|---|---|
| `bf.absence.house.message` | The default messages, one per tone, with the stand-in sentence kept as a separate field so it is only added when there is somebody to name |
| `bf.absence.me.wizard` | The "I'm away" gesture |
| `bf.partner.absence` (inherited) | The switch, the tone, and the link to the responder it armed |

## Languages

Since 18.0.1.1.0 the source strings are **English** and `i18n/fr_CA.po` carries
the French. The house messages ship in both.

* **The responder speaks the language of the person who is away.** Not the
  language of the administrator who ticked the box, and not the absence of any
  language in the scheduled job that reads the calendar: the text goes out to
  correspondents, in that person's name.
* Upgrading from 18.0.1.0.x switches a house message to English **only where it
  is still, term for term, the shipped text**. A message edited by hand is left
  as it is, in every language.
* ⚠️ Odoo rebuilds a term-by-term translated HTML field on the structure of its
  English value every time the module's catalogue is loaded. A French house
  message edited with one paragraph more or less than the English one would fall
  back to the shipped text. The 18.0.1.1.0 upgrade sets those values aside before
  that reload and puts them back after it; later upgrades carry the same risk.
