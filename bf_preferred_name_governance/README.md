# Preferred Names and Pronouns: Governance Bridge (`bf_preferred_name_governance`)

Corporate resolutions print the legal name of signatories and directors.

A resolution is an act. This bridge prints on it the **legal name** of each
person (`bf_preferred_name`), or their preferred name when they have no legal
name on file.

## What it does

In the resolution PDF of `bf_corporate_governance`, the legal name replaces the
contact name for:

- the signatories entered on the resolution (**Signatories**);
- the directors of the board, when a board resolution has no signatories
  entered;
- the **Mover** and the **Seconder** of a shareholder resolution;
- the **Director(s)** line of the minutes.

Names are read again at every print. Reprinting a resolution after a change of
preferred name still prints the legal name. The rest of the module (forms,
messages, lists) keeps the preferred name.

The bridge rebuilds the list of signatories in the same order as
`bf_corporate_governance`. If the two lists ever stop matching, for example
after a new kind of resolution is added upstream, no name is replaced and a
warning is logged. An act in the preferred name is safer than a name printed
next to the wrong person.

## Requirements

- Odoo 18.0
- `bf_preferred_name` (Symbifox)
- `bf_corporate_governance` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
