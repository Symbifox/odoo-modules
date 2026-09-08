# Célébrations (`bf_celebrations`)

A group greeting card your whole office can sign, without anyone creating an
account, and without Odoo having to reveal anyone's date of birth.

Think of the card that goes around the office in a brown envelope, except it
also works for the people who are not in the building that week.

## The decision the rest of the module is built on

`hr.employee.birthday` carries the **year** of birth, sits behind
`hr.group_hr_user`, and is absent from `hr.employee.public` — the view your
colleagues actually read. In most databases it is not filled in at all, so a
module that read it would show an empty calendar on the day it was installed.

This module therefore **never reads it**. It asks, once, and stores what the
person answers: the day and the month, without the year. That settles the
privacy question and the missing-data question in the same move, and it means
the module needs no HR permission to work.

## Consent

Four states, not a checkbox. A boolean cannot tell "never answered" from "said
no", and that difference is exactly what decides whether you ask again next
year.

| State | Effect |
|---|---|
| `pending` (default) | Nothing. One invitation, then silence. |
| `full` | Date on the calendar, group card allowed |
| `quiet` | Date on the calendar, nobody organises anything |
| `none` | Removed from everything, never asked again |

People change their own setting under **Celebrations ▸ My celebrations**. A
withdrawal takes effect **in the same transaction**: upcoming occasions
deleted, calendar entries removed, open cards cancelled. Leaving it to the
nightly job would let a card go out the same morning to someone who has just
said no.

### Opting out is invisible, and that is a rule, not a preference

`rule_profile_self` is a **global** record rule with no group: it bounds
organisers, HR staff and administrators alike. There is no count of hidden
people, no greyed-out entry, no field on the employee form. Without that,
"why was there no card for X" would stop being a question with no answer.

The invitation job ships **switched off**. It writes to real people to ask for
personal information; it turns on when the company has decided on the wording
and the moment, not because a module was installed.

## The surprise lives in the record rule, not in the screen

`rule_board_hide_from_recipient` hides the board from the person being
celebrated until it is delivered. A list view, an export, a report or a
universal search would all walk past an `invisible` attribute; none of them
walks past a record rule. The recipient is also unsubscribed from the board's
chatter at creation.

## What the module does

* A greeting board with a **public link and a QR code**. No account required.
* Messages, photos and GIFs, **uploaded**. Nothing leaves the server: there is
  no third-party image search, which would export who works here and with whom.
* Optional moderation, per-IP rate limits, security headers and `noindex`.
* Scheduled delivery. **An empty board is never delivered**: it is held back
  and the organiser gets an activity. A blank card with your name on it is
  worse than no card.
* A "thin board" nudge before delivery, under a configurable threshold.
* A reminder to the organiser, ten days ahead, with the button that creates the
  card. That reminder, not the calendar, is what makes the feature exist.
* **Typed occasions**: birthday, work anniversary and its milestones (1, 5, 10,
  15, 20, 25, 30, 35, 40 years), welcome, farewell, retirement,
  congratulations, get-well, condolences.
* Optional calendar mirror, announcement to a Discuss channel, PDF keepsake and
  a slideshow mode for the office screen.

### Work anniversaries without assuming `hr_contract`

`first_contract_date` only exists when `hr_contract` is installed. The
fallback is Odoo's own: `_get_new_hire_field` on `hr.employee.base` returns
`create_date` in Community without contracts.

## Configuration

**Settings ▸ Celebrations.**

The calendar mirror is **off by default, and an absent key means off**. A
`Boolean` bound to a `config_parameter` does not store `False` — unticking it
*deletes* the key — so the safe state has to be the state of absence. Here the
safe state is writing nothing into a calendar: an entry syncs out to phones and
CalDAV, beyond the reach of a consent withdrawal made a month later.

## Accessibility

Every theme is measured, not eyeballed. `tests/test_contraste.py` checks seven
foreground/background pairs per theme against WCAG AA on every run, because a
brand colour is not a text colour: a mid-tone accent can render around 2.6:1 on
white where 4.5:1 is required, and nothing reports it — the page renders, the
text is there, it is simply too pale for some readers. Each theme therefore
carries a separate text variant and button pair, since the correction inverts
between light and dark backgrounds.

## Requirements

Odoo 18 Community. Depends on `hr`, `mail`, `portal` and `calendar` — all core.
`qrcode` and `Pillow` are used for the QR code and image validation.

## Licence

BUSL-1.1. You may run this module in production for your own internal business
operations. Using it to provide a product or service to third parties requires
a separate agreement. Each version reverts to LGPL-3.0-or-later four years
after its release — see `LICENSE` for the exact parameters.

## Version history

| Version | Notes |
|---|---|
| 18.0.1.0.2 | Board title placeholder no longer suggests a person's first name |
| 18.0.1.0.1 | Icon titles for screen readers; consent changes are now tracked on the profile itself |
| 18.0.1.0.0 | First release |
