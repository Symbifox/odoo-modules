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
  a cross-fading slideshow for the office screen.
* **Occasions with or without a date** (2.0). A promotion or a recovery is
  celebrated when it happens; a dateless occasion stays "upcoming" until its
  card is delivered, or until someone marks it past. No reminder and no
  calendar entry, since there is no day to attach them to.

## Handwriting (2.0)

Three degrees, from the lightest to the most committed:

1. **Signatures are handwritten everywhere**, in Caveat (SIL OFL, bundled and
   served locally; no remote font is ever loaded). Signing a card is a gesture
   of the hand.
2. **A message can be shown in handwriting**: a checkbox on the public page, a
   `style` field on the message.
3. **Draw with a finger, a stylus or a mouse** on a canvas in the public page.
   The strokes are stored as **vectors** (`ink_strokes`, JSON of bounded
   coordinates), never as an image: they are redrawn in `currentColor`, so in
   the text colour of whatever theme the card wears, they weigh a few
   kilobytes, and they stay crisp in the PDF. The server keeps numbers only;
   no markup supplied by an anonymous visitor ever reaches the page.

`touch-action: none` on the canvas: without it, a finger scrolls the page
instead of writing.

## Signer groups (2.0)

`bf.celebration.signer.group`: people, departments and contacts, resolved **at
send time** (someone who joined the department since gets the link, someone who
left does not). The link goes out once per address, when the card opens and on
every "Invite signers", and the recipient is removed by **all** their known
addresses, not only by their record: a department necessarily contains them.

What a group does **not** do: say who has signed. Signing is free and
account-less, so "who has not signed yet" is not data we hold. The chatter gets
a count, never names, because the recipient reads it after delivery. The fields
are reserved to the organiser group for the same reason.

## What remains when the database has nothing left (2.0)

A card delivered inside Odoo disappears with the account, then with the
retention purge. Delivery therefore ships **with its keepsakes**, attached to
the email and downloadable from the delivered page:

* a **PDF** (the `report_board` report, in Caveat for handwritten messages);
* a **self-contained HTML page**: stylesheet, font and images inlined, no
  script, no link back to the server. It opens from disk ten years from now,
  and animated GIFs still move in it, which a PDF cannot do.

Above 15 MB an attachment is held back and the chatter says so: a mail server
that bounces silently is worth less than a link.

The person can add a **personal address** (`keepsake_email`) under **My
celebrations**: it belongs to their profile, the global rule hides it from
everyone else, and delivery reads it with sudo without ever displaying it. A
farewell card arrives the day the work address closes; that is precisely the
case it exists for.

A setting, **off by default**, deletes delivered cards after N months
(`bf_celebrations.retention_months`, daily job). 0 is the state of absence,
and the state of absence is "keep".

## The recipient's thank-you (2.1)

From their card, the recipient writes a note to the signers. It appears on the
card and on the closed signing page, and it is emailed **once**: to the people
invited by email and to those who signed while logged in, plus the organiser,
never to the recipient. People who came through the QR code without an account
read it on the page: we do not hold their address, and that is fine.

The board page is public to anyone with the link, so anyone could "thank" in
the recipient's name. Hence a **second key** (`thanks_token`), created at
delivery, that travels only in the email addressed to the recipient
(`recipient_url`). The form only appears with that key, compared in constant
time, and the thank-you can be said once. The delivery template is `noupdate`:
the 2.1.0 migration swaps `object.board_url` for `object.recipient_url` in
templates already installed.

A bridge module, `bf_celebrations_email`, adds the mail composer's **recipient
groups** as a source of signers, resolved with the inviting user's own rights.
It installs itself when `bf_email_management` is present.

## The opening (2.0)

On a delivered card: an envelope in the theme's colours whose flap lifts, the
card sliding out, then the messages arriving one by one under a fall of
confetti. All CSS; the script only orchestrates. Three restraints, because an
animation you cannot cut short is a nuisance: `prefers-reduced-motion` removes
it, a click skips it, and it plays once per browser ("Replay the opening"
brings it back).

## Four 1.0 defects fixed in 2.0

* **The default delivery time was read as UTC**: 13:00 became 09:00 in
  Montreal. It is now 13:00 in the organiser's timezone.
* **The QR code was announced on the form and never shown.** A computed
  `qr_image` displays it.
* **A due, empty board scheduled one activity per hour** (the delivery job is
  hourly). An `empty_notified` flag warns once.
* **Every image was served as `image/png`** under `X-Content-Type-Options:
  nosniff`, GIFs included. The type now comes from the bytes.
* Email buttons painted `#29ABE1` under white text (2.62:1) move to a measured
  `#177AA3`. The templates are `noupdate`: the 2.0.0 migration recolours the
  ones already installed, only if they still carry the old colour.

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
| 18.0.2.1.0 | Recipient thank-you (keyed link, once, emailed to signers); signer-source hook for the `bf_celebrations_email` bridge |
| 18.0.2.0.0 | Handwriting (font, style, vector ink), signer groups, delivery opening animation, PDF + self-contained HTML keepsakes, personal keepsake address, optional retention purge, dateless occasions; four 1.0 fixes (UTC delivery hour, missing QR, hourly activity, image MIME type) |
| 18.0.1.0.2 | Board title placeholder no longer suggests a person's first name |
| 18.0.1.0.1 | Icon titles for screen readers; consent changes are now tracked on the profile itself |
| 18.0.1.0.0 | First release |
