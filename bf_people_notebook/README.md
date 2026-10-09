# People I've Met (`bf_people_notebook`)

A private notebook of the people you cross paths with, on a trip or closer to home:
where you met, what they like, what they know about you, and the detail that lets you
pick the conversation up again. A card is **not a contact**: nothing reaches the
address book until you decide so.

**For whom**: a Symbifox **household instance** (the Symbifox Personal plan), where
several people of one home each have an account. It also installs on any Odoo 18
Community that has the Notebook (`bf_bloc_notes`).

## What it does

* **A card needs one clue only**: a first name, ticked *Not sure of the name* when you
  only half remember it (shown "Ondine ?", still found by the search), a description
  that brings them back to mind ("the cyclist with the red helmet"), or a photo.
  Everything else is optional. There is no gender field.
* **Where you met**: the place, the city and country, the **occasion** (a trip, an
  event, a season: "Gaspésie 2026"), the date (exact, or the month or the year only,
  or none) and who introduced you.
* **About them**: a **circle** (family, friend, met on a trip, work, acquaintance),
  **interests** as tags, what they like, **what they know about you**, and **to
  reconnect**: something they noticed, a plan you talked about.
* **Your notes stay notes.** A note from the Notebook is linked to the card (the
  **Notes** button). **Last seen** is the most recent of the meeting date and your
  latest linked note, with nothing to type.
  **Make a person card** on a note opens a new card that the note joins; saved
  without a clue, the card takes the note's title as its description.
* **One search box**, *Anything I remember*, finds a card by any of its fields and by
  the text of your notes linked to it. Filters: not a contact yet, to reconnect, name
  not sure, each circle, shared with me. Group by occasion, country, city, circle or
  year met. With PostgreSQL's `unaccent` enabled on the database, "Gaspesie" finds
  "Gaspésie".
* **Make a contact** when the time comes (the card needs a name): a contact with the
  name, city, country and photo, nothing else. The card keeps the link and stays
  private.
* **Remind me to reconnect** opens Odoo's activity scheduler with the *Reconnect*
  type (three months ahead by default). The reminder is an activity: it shows in your
  activities and under the *To reconnect* filter. Nothing is scheduled unless you ask,
  and no email is sent.

## Who sees what

A card is **private to the person who wrote it**, with its photo, attachments,
thread, followers and activities. Its owner may **share** it, read-only, with chosen
members: they read the card, its photo, its thread and its activities; they cannot
change it, follow it or copy it, and they do not see the owner's private notes (a note
the owner shares in the Notebook stays readable by the whole household). Occasions and
interests belong to their owner and are readable through a card shared with you.
Only the owner's notes can be linked to a card, and no one other than the owner can
be mentioned or notified on it.

Guards the module adds to the access rules, because Odoo does not keep them alone:

* the record rules are **global**: a group rule added elsewhere ("administrators see
  everything") cannot widen them;
* the owner of a card, an occasion or an interest never changes;
* only the owner follows a card, and a message on it notifies the owner only;
* an activity on a card always belongs to its owner: Odoo lets an assignee read an
  activity, and emails it, even without access to the record;
* the followers of a card cannot be listed by anyone who cannot read it
  (`mail.followers` is readable by every internal user, with no rule);
* where Odoo reads a name with elevated rights (a debug-mode access error, a link
  from another record), a card you cannot read is called "Private card";
* an access refusal is the same for everyone who cannot read the card: it never tells
  who owns it;
* nothing reaches someone else's card by a side door: an activity, an attachment or
  a note link moved there, an interest attached through its Many2many, a notification
  forged with `message_notify`, a copy made by a reader;
* a card files only its owner's occasion and interests, and is shared with active
  internal users only.

The access rules apply to a system administrator (`base.group_system`) too, and the
tests prove it. They are no guarantee against one: that group can change the rules
themselves, and the Notebook's own rules already give it every note, including the
notes linked to a card. In a hosted household instance, no member has that group.

## What it does not do

No sync with the phone's contacts, no enrichment from social networks, no birthdays
(that is `bf_celebrations`, on contacts), no map, no email reminders, no mobile screen
of its own: capture goes through Notebook notes, offline and by voice, and cards open
in the browser.

## Technical notes

* Depends on `base`, `mail` and `bf_bloc_notes`.
* Models: `bf.people.person` (the card), `bf.people.occasion`, `bf.people.interest`.
* Source strings in English; French in `i18n/fr_CA.po`, written in gender-neutral
  French.
* Tests: `--test-tags /bf_people_notebook`; every refusal is played as a non-admin
  member (and a system administrator, kept out). One of them is a canary: for every model another
  member can read, every text field, link and record name is searched for the words of
  a private card.

## License

LGPL-3. See `LICENSE`.
