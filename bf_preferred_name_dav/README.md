# Preferred Names and Pronouns: CardDAV Bridge (`bf_preferred_name_dav`)

Pronouns and grammatical gender in the contact cards served over CardDAV.

The address books that `bf_dav_server` serves to phones and email clients now
carry, for each person who gave them, their **pronouns** and **grammatical
gender**, as the `PRONOUNS` and `GRAMGENDER` properties of RFC 9554 (IANA vCard
elements registry). The address book then knows how to write to the person
without guessing from a first name.

## What it does

- Adds `PRONOUNS` and `GRAMGENDER` to the card of each contact person who has
  pronouns or a grammatical gender (`bf_preferred_name`). Nothing is added for
  a company or for someone who gave nothing.
- Serves the same card on every path: a single GET, the grouped requests that
  phones send (multiget, query, sync-collection) and the synchronization
  journal.
- Changing a contact's pronouns or grammatical gender changes the ETag of its
  card and records the change, so that syncing phones fetch it again.
  Installing and uninstalling the bridge do the same for every card concerned.

Cards are still served as vCard 3.0 (`bf_dav_server`). `PRONOUNS` and
`GRAMGENDER` are vCard 4 properties: a client that does not know them ignores
them.

## Outgoing only

Pronouns are given in Odoo, by the person on the portal or by someone who has
them from the person. What a phone sends back in these two properties is
neither imported nor kept with the card's extra data. Otherwise the card would
carry them twice after the next round trip.

A user's personal cards (their own address book) are not touched.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_dav_server` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
