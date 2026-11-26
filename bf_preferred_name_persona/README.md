# Preferred Names and Pronouns: Persona Bridge (`bf_preferred_name_persona`)

The recipients' pronouns in the composer banner.

Before you write, the composer banner of `bf_persona` recalls how a contact
likes to be addressed. That banner is drawn per persona, and a company persona
covers all of its people: what it says describes the company, not the person
you are writing to.

## What it does

- Adds to the banner, for each recipient in **To** and **Cc**, the
  **pronouns** and **grammatical gender** they gave (`bf_preferred_name`),
  whether or not they have a persona. Bcc recipients and companies are left
  out.
- Each line reads the recipient's name, then the pronouns and the grammatical
  gender, for example "**Sam Exemple**: they/them · Neutral (epicene)". The
  colon follows the reader's language ("**Sam Exemple** : …" in French), as in
  the banner of `bf_persona`.
- Everything entered by people is escaped.
- Beyond five people, the rest is counted ("… and 2 more"), in the user's
  language.
- The **Appliquer le persona** button of `bf_persona` shows only when there is a
  persona to apply. A recipient who gave pronouns but has no persona makes the
  banner appear, not the button.

Nothing is said about someone who gave nothing: a banner that repeats "not
specified" for everyone stops being read.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_persona` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.1**: The colon after each name follows the reader's language, like
  the rest of the banner (`bf_persona` 18.0.3.3.2).
- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-11-25 for this version.
