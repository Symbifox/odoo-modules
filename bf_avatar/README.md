# Symbifox Avatars (`bf_avatar`)

An avatar in your house style for anyone without a photo, and a builder so
each person can make their own character.

## Why

Hardly anyone uploads a photo. Most screens therefore show Odoo's default
avatar, a white initial on a colour picked at random, and every contact
without a picture becomes the same grey silhouette. This module replaces that
default, and only that default.

## What it does

- **A style per database** (General Settings, *Avatars*):
  - *Initials in the house colours*: shades of the company colours that keep
    white text at a 4.5:1 contrast ratio or better.
  - *Character: Open Peeps* (hand drawn, in colour) or *Character:
    Notionists* (line art), on a pale tint of the house colours.
  - *Odoo default*, to leave Odoo's own generator alone.
- **Deterministic.** The avatar is derived from the name, so the same person
  always gets the same one, on the user, the partner and the employee alike.
- **Only generated avatars are ever redrawn.** Changing the style redraws
  every avatar that Odoo or this module generated. An uploaded picture is
  never touched, SVG uploads included: the pass recognises Odoo's generated
  SVG by its exact shape and its own by a marker attribute.
- **Contacts too.** A person contact without a picture gets a generated
  avatar instead of the grey silhouette (a setting turns this off). Companies,
  delivery and invoice addresses keep Odoo's icons.
- **My avatar**, in the user menu (character styles only): pick hair,
  expression, facial hair, mask, glasses and colours. Each thumbnail shows
  your avatar wearing the part. If you have an uploaded picture, you are asked
  before it is replaced. *Start over* forgets your choices.
- **Uninstall** hands generated avatars back to Odoo's original style.

## Security

- The SVG is assembled on the server from the style's own templates. Every
  value sent by the builder is checked against the style: an unknown part or
  a colour outside the style's palette is dropped, so nothing a user types
  reaches the markup.
- Odoo stores an SVG written by a user who cannot edit views as plain text,
  even under `sudo()`. Because the content is generated, the module writes it
  as the superuser.
- The builder's methods take no user argument: they act on the caller only,
  and portal users are refused.
- No photo or name is sent to any outside service.

## Requirements

- Odoo 18.0, `base_setup`, `web`.
- With Fox Quest installed, `bf_avatar_gamification` installs itself and
  sells decorative parts for XP.

## Translations

French (Canada) in `i18n/fr_CA.po`. Source strings are English.

## Credits

- Open Peeps by Pablo Stanley and Notionists by Zoish, both CC0 1.0 (public
  domain).
- The part templates in `data/styles/*.json` were exported from the DiceBear
  9.4.2 packages (MIT, Florian Körner) by `data/styles/export.mjs`; for the same
  choices, the drawing is the same as DiceBear's. The MIT notice and the
  artwork credits ship in `data/styles/LICENSE-DiceBear.txt`.

## Changelog

### 18.0.1.0.1

- First public release.
