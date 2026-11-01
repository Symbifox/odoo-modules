# Preferred Names and Pronouns: Signatures Bridge (`bf_preferred_name_signature`)

The pronouns in the email signature are the pronouns on the person's contact.

`bf_multi_company_email` shows pronouns next to the name in the email
signature, from a field on the user. `bf_preferred_name` keeps pronouns on the
person's contact, where the person sets them, the portal included. Two fields
for the same thing end up contradicting each other. This bridge makes them one.

## What it does

- The signature **Pronouns** field becomes the contact's field
  (`partner_id.bf_pronouns`). Changing it in the user preferences, on the
  contact or on the portal changes the same value.
- The signature is **rendered again** when the pronouns change, wherever the
  change comes from.
- The user preferences show the pronouns **once**, under the email address. The
  notice shown when the company enforces its signature points there.
- On installation, the pronouns already entered for the signature are **copied
  to the contact** when the contact has none. When the contact already has
  different pronouns, the contact wins and the user is reported in the log by
  number, never by value. Signature pronouns that the contact field refuses
  (anything other than letters and a few signs) are not copied, and are
  reported the same way.
- On uninstallation, the contact's pronouns are **copied back** to the
  signature field, so the signature does not return to the pronouns it had
  before. Values the installation left behind (empty contact, refused value)
  are kept as they are.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_multi_company_email` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-31 for this version.
