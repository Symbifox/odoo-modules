# Reading out-of-office replies (`bf_contact_absence_mail`)

Bridge between the unified mailbox and contact absences: when a contact answers
"I am away until the 15th", the information already reached you. This module
picks it up.

Depends on `bf_contact_absence`, `bf_email_management` and `bf_ai_bridge`.

## The AI assistant is optional

`bf_ai_bridge` is a deliberately bare leaf module, installable on a tenant that
has no assistant at all: that is what lets this module require it outright
without imposing the assistant. Recognition and date reading are written in
plain code; the bridge is called only if its socket answers, and only to catch
the prose that regular expressions cannot. **Without the assistant the module
works**, with a readable period four times out of five.

## What a real corpus imposed

All of the recognition comes from a read-only pass over more than twelve
thousand received emails, not from intuition.

* 🔴 **`Auto-Submitted` is not enough, and accepting too broadly is ruinous.**
  RFC 3834 §5 distinguishes `auto-replied` (a personal reply produced by a
  machine) from `auto-generated` (a notification). Accepting anything that is
  not `no` produced **1 510 false positives out of 1 531**: backup reports,
  appointment confirmations, cloud notifications and the morning digest all
  carry the latter.
* 🔴 **`X-Auto-Response-Suppress` is not a positive signal**: it is an Exchange
  instruction telling the recipient not to auto-reply. Your own notifications
  set it.
* 🔴 **The subject does the heavy lifting, and French comes first.** Twenty of
  the twenty-nine replies found carry no automatic header at all. ⚠️ French
  Outlook writes `Réponse automatique\xa0:` with a **non-breaking space**: a
  filter using an ordinary space sees nothing.
* 🔴 **Your own domains must be excluded.** Some service mailboxes write from
  partner records that carry no user, so an "internal contact" test misses them;
  the domain is what settles it.
* **"from the 20th to the 31st of July": the first day has no month.** Without a
  dedicated interval pattern, a twelve-day shutdown shrinks to the 31st.
* **The cue is read NEXT TO the date, not anywhere in the message.** "I am out of
  the office until 17 March. For a call back, email support@..." lost a day
  because of a "back" that belonged to a phone call.

After those corrections the pass keeps **25 replies out of more than twelve
thousand emails**, of which **20 carry a readable period without the assistant**.

## It proposes, it does not write

A recognition creates a `bf.partner.absence.suggestion` that a human accepts or
rejects in one click. Three reasons:

1. nine replies out of twenty-eight carry no readable date;
2. the net catches robots: `mailer-daemon` came through twice;
3. a wrong record is worse than an empty one, because a false warning gets
   disarmed and takes the true ones with it.

A recognised message with **no date at all** that speaks of an application or a
received request is filed as an **acknowledgement** and rejected on the spot: the
trace stays, the queue stays clean. A dated message is always an absence.

The stand-in is matched on the **email address** only, never on a name: getting
the stand-in wrong sends a client's mail to somebody else.

## Privacy

None of the reply's text is copied onto the contact record: a period, a nature,
at most a stand-in, and a link to the original email, which stays in the unified
mailbox under its own retention rules.
