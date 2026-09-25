# Symbifox Avatars for Fox Quest (`bf_avatar_gamification`)

Unlock avatar parts with the XP earned in Fox Quest.

## What it does

- **Six packs of decorative parts**, three per character style, created as
  ordinary Fox Quest rewards at 25, 50 or 100 XP (prices can be changed in the
  shop; one of each per person):
  - Open Peeps: expressions, sunglasses and eyepatch, hats and bold cuts.
  - Notionists: outfits, gestures and hat, badges.
- **What says who a person is stays free**: hair textures, head coverings,
  glasses, beards, skin and hair colours. A default (drawn) avatar never uses
  a part that is for sale.
- **In the builder**, a locked part shows its price and can be tried on;
  *Save* stays disabled until the pack is unlocked, in one click.
- **Self-service.** Claiming a pack approves it; nobody has to approve a
  hairstyle. A pack for a style the database does not use cannot be claimed.
- **It never costs a level.** The pack comes out of the Fox Quest XP balance,
  not out of the XP earned (Fox Quest 18.0.2.6.1 and later).
- A reward carries its style and its parts (`part:variant`, one per line),
  checked against the style when saved.

## Requirements

- `bf_avatar` and `bf_gamification` (18.0.2.6.1 or later). Installs itself
  when both are present.

## Translations

French (Canada) in `i18n/fr_CA.po`. Source strings are English; the pack
names are data, in French.

## Changelog

### 18.0.1.0.1

- First public release.
