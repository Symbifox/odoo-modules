# Preferred Names and Pronouns: Signature Bridge (`bf_preferred_name_sign`)

The signer's legal name on the act, their preferred name everywhere else.

A `bf_sign` signer carries a single name, frozen when the request is sent. It
served both the **act** (the signing certificate, the "name" field stamped on
the PDF, the public verification page) and **courtesy** (the greeting of the
emails, the portal). Using the legal name there would have greeted the person
by that name in every email. And `bf_sign` sometimes copies the signer name to
a contact it creates at sending: the legal name would have become that
contact's preferred name.

This bridge separates the two.

## The name on the act

When a request is sent, the legal name of the signer's contact
(`bf_preferred_name`) is **frozen** in a separate field, **Name on the act**,
next to the signer name. This happens only when:

- the contact has a legal name;
- the contact is a person, not a company;
- the contact's email is the signer's email;
- for a signer linked by email alone, exactly one person contact has that
  email.

Otherwise the act uses the signer name. The signing certificate, the stamped
"name" field and the public verification page read the name on the act. A legal
name changed after sending does not change an act already sent.

Name on the act is readable only by the **Legal names** group. The certificate,
the stamping and the public pages read it internally. Outside the group, the
value column of a request's fields shows the signer name instead.

## The preferred name

Emails, the portal, the signature log and the contact created at sending keep
the signer's preferred name. No path writes the name on the act to a contact.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_sign` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
