# Symbifox Contact Absences (`bf_contact_absence`)

Know that a contact is away **at the moment you write to them**, not three days
later when you finally read their auto-reply.

Depends on `contacts` and `mail` only. It installs on a tenant that has no
unified mailbox, no SMS and no AI assistant.

## Why this module exists

Every sales engagement platform on the market detects out-of-office replies, and
they all use it for the same thing: **pausing an outbound sequence**. None of
them warns a human who is writing an ordinary email, none warns before a phone
call, none carries the absence as shared knowledge on the contact record, and
none tells a company shutdown apart from one person's holiday.

The need is rare and the mistake is expensive. Over fourteen months of real mail
on one instance: twenty-eight auto-replies received, and **four absences during
which somebody wrote anyway**, six messages in all, three of them to the same
tax lawyer in the middle of a filing.

## What it adds

* **A dated period on the contact record**: from, to, nature, and above all
  **the stand-in**, meaning who to write to in the meantime. The "away until"
  and "back on" fields correct each other, because auto-replies use both forms
  and a one-day slip would put the banner on screen the very morning the person
  is back.
* **A banner on the chatter**, so it shows on a task, an opportunity, a ticket
  or any record with a chatter, without this module depending on any of them.
* **A banner in the full mail composer**, which also covers a unified-mailbox
  reply when one is installed, since that opens the standard composer.
* **"Schedule for their return"**, one click, backed by Odoo 18's native
  `mail.scheduled.message`. It sends the morning after they are back: nobody
  wants to be the first email in a mailbox with four hundred unread.
* **A follow-up reminder** the day after the return, carrying the subject of the
  last exchange so it is actionable rather than a blank "check in".
* **Company shutdowns**: an absence recorded on a company warns for every one of
  its contacts, with wording that says "closed" rather than "away".
* **A seeding wizard for known industry shutdowns**, for the trades whose
  collective holidays are published a year in advance.

## What it does not do

**It never blocks a send.** Writing to someone on holiday is often exactly what
you want, so that it is waiting for them. The banner informs and offers; it
stops nothing.

It stores **no medical reason**: the nature comes from a short fixed list
(holiday, leave, shutdown, training, other), and the module never copies the
free text of an auto-reply onto a contact record.

## What it deliberately does not borrow from `hr_holidays`

Odoo already knows an **employee** is on leave, and publishes
`out_of_office_date_end` on their partner. Three reasons not to hook into it:

1. it only works for a partner that carries an internal user;
2. the display lives in Discuss only (the banner is bound to
   `props.thread.model === 'discuss.channel'`), so a record's chatter shows
   nothing;
3. `hr_holidays` overrides `res.partner._to_store` and writes
   `out_of_office_date_end: False` for a partner with no user, which would
   overwrite a value set by another module.

Only the vocabulary is borrowed.

## Technical notes

* `res.partner.bf_is_away` and `bf_away_until` are **non-stored computed fields,
  each with its own `search` method**. Without one, a criterion on a non-stored
  computed field is dropped silently and the filter returns the whole database.
  Storing them would be worse: their value depends on today's date.
* **The end date is mandatory.** An absence with no return leaves a warning lit
  forever, and a warning that cries wolf gets ignored.
* Two overlapping absences on the same contact are refused.
* The wording carries **no gender agreement**: Odoo holds no gender on a contact
  record, and guessing it from a first name gets real people wrong.
* ⚠️ On a thread that **is** a contact record, Odoo refuses the write to an
  employee without the "Contact Creation" right; the native
  `action_schedule_message` is refused at the same place. From a task, an
  opportunity or a unified mailbox, an ordinary employee goes through.
