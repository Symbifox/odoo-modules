# Preferred Names and Pronouns (`bf_preferred_name`)

The preferred name everywhere, the legal name kept apart and protected, and
the pronouns the person chose.

Odoo has a single field for a person's name, and that field is tracked: renaming
a contact writes "old name → new name" in its chatter, readable by anyone who
opens the contact and sent to its followers. There is no safe place for a
person's legal name, and nowhere to record pronouns, so any text written about
someone can only guess from a first name. This module fixes those three gaps.

## What it does

### The contact name is the preferred name

`name` stays the name the person uses. Nothing is layered on top of it: email
headers, mentions, search and exports all read the same name as the screen.

### The personal legal name, kept apart

A **Personal legal name** field (`bf_legal_name`) holds the legal name of a
person when it differs from their preferred name.

- Only members of the **Legal names** group can read, search or write it. The
  field is removed from the contact form for everyone else, and creating a
  contact with a legal name outside the group is refused.
- It is never tracked in the chatter.
- The group is empty at installation, administrators included.
- Only the acts that require it read it: `_bf_legal_name()` returns the legal
  name, or the preferred name when the legal name is empty. The bridges of this
  family print it on corporate resolutions, donation receipts and electronic
  signatures (see Requirements). Nothing else displays it.

### Pronouns and grammatical gender, given by the person

Two fields, hidden on companies:

- **Pronouns** (`bf_pronouns`), in the person's own words, up to 40 characters
  (for example "she/her", "il", "iel", "they/them").
- **Grammatical gender** (`bf_grammatical_gender`): Feminine, Masculine or
  Neutral (epicene). The values follow vCard 4.0 `GRAMGENDER` (RFC 9554). It
  tells how to make words agree when writing about the person in French. Left
  empty, text is written in gender-neutral (epicene) language.

The person sets them in three places:

- on the portal, in the account details page (`/my/account`), with suggestions
  and a "Not specified" choice for the grammatical gender;
- in their own user preferences;
- through an internal user who has them from the person, on the contact form.

Pronouns accept only letters of any script (with their combining marks), spaces
and `/ , · - ' ( )`. Digits, quotes, colons, line breaks, modifier letters that
imitate punctuation (such as "ː" or "ʺ") and invisible variation selectors are
refused, on every path, the portal included. The error reads: "Pronouns may
only contain letters, spaces and these signs: / , · - ' ( )."

Nothing is ever guessed. No pronoun, gender or title is derived from a first
name. An empty field means "not specified".

### Change preferred name

**Change preferred name**, in the Action menu of a contact form, renames the
contact without leaving the former name in its chatter. The linked employee
record is renamed the same way when the Employees app is installed, with the
rights of the user who renames. If that user cannot edit the employee, the
contact is renamed and a warning asks Human Resources to use the same action.

Members of the Legal names group get two more options in the same dialog:

- **Keep the current name as legal name**, offered only when the contact has no
  legal name yet;
- **Erase the name-change history**, which removes every "old name → new name"
  trace from the contact's chatter and from the linked employee's.

The wizard that carries the new name is readable only by the user who opened
it.

### Renaming oneself

A person who changes their own name, on the portal, in the shop address form,
in their preferences or through the API, leaves no trace in the chatter of
their contact, nor in that of their employee record. Renaming someone else's
contact by hand stays tracked: Change preferred name is the action that does it
without a trace.

### Contact merge

When contacts are merged, the kept contact receives the legal name of the
merged one if it has none, even when the merge is done by someone outside the
Legal names group. When the legal names differ, nothing is overwritten and the
kept contact is logged on the server by its ID only, never by name.

### Writing guidance for Gen

`_bf_writing_guidance()` writes, in a single place, how to write about a
person: the pronouns they gave, quoted as given, and the grammatical agreement
to use, epicene when they gave nothing. It returns an empty string for a
company. The text is in French, like the rest of what Gen receives about a
contact.

The Gen assistant (`bf_claude_chat`) passes it for the user who talks to it and
for the contact of the record in context. The computed field **Writing
guidance for Gen** (`bf_writing_guidance`) exposes the same text to API
clients.

## What it does not do

- It does not rewrite history. Emails already sent and messages already written
  keep the name they carried. Tracking notifications already emailed to
  followers cannot be recalled.
- "Erase the name-change history" only touches the contact and its employee
  record. Other records that tracked the contact, such as the customer of a
  task, keep the name it had then.
- The portal opens only the pronouns and the grammatical gender. The legal name
  is neither readable nor editable there.

## Requirements

- Odoo 18.0
- `mail`, `portal` (Odoo)
- Optional: `hr`. When the Employees app is installed, the linked employee
  record follows the renaming.

Bridges, all Symbifox modules, install themselves when their other dependency
is present: `bf_preferred_name_dav`, `bf_preferred_name_governance`,
`bf_preferred_name_linkpage`, `bf_preferred_name_nextcloud`,
`bf_preferred_name_persona`, `bf_preferred_name_receipt`,
`bf_preferred_name_sign` and `bf_preferred_name_sign_sale`.

## Changelog

- **18.0.1.1.0**: `_bf_writing_guidance()` writes, in one place, a person's
  writing guidance for Gen (their pronouns, agreement in the grammatical gender
  they gave, epicene otherwise). Gen's context reads it, and the
  `bf_writing_guidance` field exposes it to API clients.
- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
