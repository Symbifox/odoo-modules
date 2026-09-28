# Symbifox École: authorisations (`bf_school_forms`)

Field trip, activity and overnight authorisations, answered by the guardians in one
click, with dated evidence.

## Why

Paper slips get lost in backpacks, and a portal that asks a parent to sign in before
answering loses the parents who never did. Parents also disagree: when one says yes
and the other says no, the school needs a rule written down before the trip, not a
phone call the morning of.

## Features

- The school office writes the authorisation (type, groups, date, text, deadline) and
  sends it. One answer per student enrolled today in those groups.
- One email per guardian who signs (`can_sign` on the guardian link), naming the
  child, with a **personal link** that works without a portal account. Parents with
  an account also find it under **Authorisations** in the family portal.
- **I authorise** / **I refuse**: one click, final. To change it, the family calls
  the school office.
- **One guardian is enough** by default (Civil Code art. 603: towards a third party in
  good faith, one parent is presumed to act with the other's agreement). A type may
  require every guardian who signs; "Overnight stay" does.
- **A refusal always wins**: one parent's "no" keeps the child home, even if the other
  said yes.
- Evidence per answer: date, channel (portal or personal link), signed-in user if
  any, IP address, browser, and the SHA-256 fingerprint of the text as sent. A sent
  text cannot be edited, so the fingerprint proves what the guardian answered.
- Reminder in one click; a daily cron closes the authorisations past their deadline
  and the students still waiting show "No answer".
- Students without any guardian who signs are counted at sending, as are signing
  guardians without an email address.

## Security

- The school office creates, sends, reminds and closes. Teachers read who is
  authorised, not the evidence (IP address, browser): that stays with the office.
- Nobody writes an answer by hand: answers come from the guardians only.
- Portal pages read through the adult's own answers; another family's answer is a
  404. The personal link is checked in constant time, and works only while the adult
  still signs for the student.

## What has not been confirmed

- No payment with the authorisation yet.
- Teachers cannot send an authorisation to their own groups yet.
- Medication and health forms are deliberately not covered: they need a health
  record with restricted access.
