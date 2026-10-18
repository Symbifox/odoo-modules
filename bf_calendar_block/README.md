# BF Calendar Work Blocks

Book typed blocks of time in the calendar (focus, production, meeting slot,
catch-up), each shown with its own pictogram and colour, and carried to other
calendars as a category.

## Why

Before this module, the kind of a block lived only in its title ("PROD",
"Catch-up", "Focus time"): such blocks looked like any other event in the
calendar, and nothing else could tell them apart.

## How it works

- A **work block type** is a calendar tag (*Calendar > Configuration > Meeting
  Types*) marked *Work block*, with:
  - a **pictogram**, shown before the title of the block in the calendar;
  - a **colour** (the tag's own colour, applied by the automatic colour rule
    *Work blocks* of `bf_color`, which can be changed);
  - an **availability** given to new blocks (*Busy* by default: online booking
    no longer offers that time);
  - an optional **privacy** given to new blocks;
  - an **order**, used for the buttons of the quick-create dialog.
- Four types are created at installation, named in the administrator's language (else the company's):
  Focus 🎯, Production 🔨, Meeting slot 🤝 (*Available*: it opens time instead
  of closing it), Catch-up 🔁. A tag with the same name that already exists
  (from Nextcloud, for instance) is reused.
- In the calendar, the quick-create dialog starts with one button per type.
  One click sets the type, the title (until a title is typed by hand), the
  availability and the privacy. Clicking the chosen type again removes it.
- An event's type is its first work block tag. Choosing a type sets the tag;
  a tag set elsewhere (Nextcloud, a phone over CalDAV) gives the type back.
  With `calendar_nextcloud_sync` 18.0.2.26.0 or later, tags travel as
  `CATEGORIES` (RFC 5545), which Nextcloud, Thunderbird, Apple Calendar and
  DAVx5 display.
- On someone else's private event, the type and the pictogram are hidden like
  the title: the calendar shows *Busy* only.
- *Work blocks* filter and *Work block* grouping in the calendar search.

Meeting slots open online booking with `bf_calendar_block_appointment`.

## Requirements

- `calendar_nextcloud_sync` 18.0.2.26.0 or later (CATEGORIES to and from Nextcloud).

## Not yet

- Symbifox Mobile.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.0 | 2026-10-14 | Typed work blocks: block types on calendar tags (pictogram, colour, availability, privacy, order), four types at installation, type buttons in the quick-create dialog, pictogram before the title in the calendar, colour rule, search filter and grouping |

## License

LGPL-3.0
