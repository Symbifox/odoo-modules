# Preferred Names and Pronouns: Nextcloud Contacts Bridge (`bf_preferred_name_nextcloud`)

Pronouns and grammatical gender in the cards pushed to Nextcloud.

The cards that `contacts_nextcloud_sync` builds for the Nextcloud address book
now carry, for each person who gave them, their **pronouns** and **grammatical
gender**, as the `PRONOUNS` and `GRAMGENDER` properties of RFC 9554. Nothing is
added for a company or for someone who gave nothing.

## What it does

Changing a contact's pronouns or grammatical gender marks the contact to be
pushed again, as `contacts_nextcloud_sync` does for its own fields. The
conditions are the same: the contact is linked to Nextcloud, its sync
configuration is active, and Odoo is the source of the contact.

The contact is marked only when the **Sync Direction** is "Odoo → Nextcloud
only". With "Nextcloud → Odoo only" or "Bidirectional", marking a card would
make the sync read it back from Nextcloud and overwrite the contact, so it is
not marked.

At installation, contacts that already have pronouns are marked the same way.
At uninstallation, they are marked again, so that their cards are pushed
without the pronouns.

## Outgoing only

The sync from Nextcloud to Odoo does not read these properties. Pronouns are
given in Odoo.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `contacts_nextcloud_sync` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
