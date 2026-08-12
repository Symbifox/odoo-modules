# Outreach campaigns (`bf_outreach`)

An Odoo 18 module for running call, email and letter outreach campaigns, where the
follow-up cadence is a property of the campaign rather than a discipline each person
has to keep in their head.

Built for the case where a prospecting list lives in a spreadsheet and nobody can say
who is overdue, who has never been reached and who replied.

## Features

- **Cadence per channel**: a quota and an interval for calls, emails and letters, set once per campaign
- **A computed next contact date** per target, stored so filters are always accurate
- **Stages** (kanban), configurable globally or per campaign, with `won` / `lost` types that close the file
- **Interaction log**: every call, email, letter, text, meeting or LinkedIn touch with its outcome, duration and summary
- **Do-not-contact**: an exclusion that propagates to the Odoo contact and to every campaign, blocks imports and blocks sending, with who/when/why kept
- **Deduplication** on normalised email and E.164 phone, not only on the linked contact
- **Campaign email template** with one-click send from a target, logged automatically, including mass sends
- **Campaign metrics**: coverage, reply rate, conversion, progress, overdue and due-today counts
- **Daily reminders**: one activity per campaign, one per due target, or none
- **CRM hand-off**: create an opportunity carrying the full interaction history, with the originating campaign recorded on the lead
- **Search filters**: due today, overdue, never contacted, replied, no reply, open files, converted, excluded
- **Views**: kanban, list, form, calendar, activity, pivot and graph

## Dependencies

- `mail`, `contacts`, `crm`, `phone_validation`
- `bf_onboarding_base` (guided welcome panel)

## How the cadence is computed

Each campaign carries, for each of the three channels, a quota per target and an
interval between two contacts of the same kind. For every target the module derives
three independent dates:

- **Next call** — last call + interval. With no call yet, the target is due from the
  campaign start date (or from the date it was added, whichever is later). Nothing once
  the call quota is met.
- **Next email** and **next letter** — the same logic with their own interval. The letter
  quota defaults to 0, so a campaign that posts nothing never sees that channel.

`next_action_date` is the earliest of the three. On a tie the order is call, email,
letter (the `CHANNELS` constant in `models/outreach_target.py`). This field drives the
"due" and "overdue" filters, the calendar view and the daily reminders.

The cadence stops in four cases:

1. the target reaches a `won` or `lost` stage;
2. the campaign is paused, closed or cancelled;
3. the target replied and the campaign has "stop at the first reply" enabled;
4. `paused_until` pushes the date out — this field delays the cadence, it does not
   suspend it.

With "working days only", a follow-up that would land on a Saturday or Sunday moves to
the Monday.

These dates are **stored**. They are recomputed when an interaction is logged or a
campaign setting changes, never as a function of the current time. Nothing to refresh,
and no scheduled action needed for the filters to be correct.

## Do-not-contact and CASL

The **Do not contact** button records the exclusion on the target, propagates it to the
Odoo contact (`res.partner.outreach_opt_out`), freezes the cadence, removes pending
activities and logs the refusal with who recorded it, when, and in whose words. Imports
then refuse that contact in any campaign, and sending refuses it too.

> **Not to be confused with `res.partner.do_not_contact`.** That field belongs to the
> `privacy_consent` module: it is a *computed* field derived from
> `privacy.contact.preference`. This module does not redefine it. It **reads** it at the
> moment of acting, through `res.partner._outreach_is_blocked()` and
> `bf.outreach.target._is_solicitable()`. Hence the split: `is_excluded` is the stored,
> filterable flag owned by this module, while `_is_solicitable()` is the last-moment
> check that also consults the consent register, because consent can be withdrawn at any
> time.

## Bridge modules

Installed automatically (`auto_install`) as soon as both sides are present:

| Module | What it does |
|---|---|
| `bf_outreach_email` | An email received from a target becomes an inbound interaction, so "stop at the first reply" needs no manual entry |
| `bf_outreach_call` | Calls from `call.archive.call` become interactions with their real duration. Covers the softphone too, since it feeds the same archive |
| `bf_outreach_appointment` | A confirmed booking logs a meeting interaction and moves the target to the "meeting booked" stage; the booking type's public URL is exposed on each target |

Each bridge advances a date watermark in `ir.config_parameter` and carries a link back to
its source (`bf_email_id`, `call_archive_id`, `booking_id`), which guarantees the same
event is never logged twice.

Two further integrations need no code of their own:

- **Click to call** — the standard `phone` widget renders a `tel:` link, which the
  softphone module intercepts.
- **Letter merge** — the list action "Prepare the letters" creates any missing contacts,
  then opens the batch letter merge. The letter module is detected at runtime rather than
  declared as a dependency.

## Security

Two groups: *User* (run campaigns) and *Manager* (configuration and deletion). The three
main models carry a company record rule.

## Testing

```bash
odoo -d <db> -u bf_outreach --test-enable --test-tags bf_outreach --stop-after-init
```

Covers the cadence on all three channels, quota exhaustion, the working-day shift,
`paused_until`, stop-on-reply, closing stages, the exclusion and its propagation, the
consent-register gate, deduplication, normalisation, the two reminder modes, the CRM
hand-off, the mass-send logging path and the three bridges.

## Notes

- The shipped stages (`data/outreach_stage_data.xml`) are `noupdate="1"`: they can be
  edited without being overwritten on upgrade, but a fix in the module does not propagate
  to them either.
- Campaign counters are computed on read (not stored) from `read_group`, so they are not
  sortable in list view. This is deliberate, to avoid cascading invalidation on large
  lists.
- Translations live in `i18n/` (`.pot`, `fr_CA.po`, `en_CA.po`). An upgrade does **not**
  reload them: pass `--i18n-overwrite` after changing any user-visible string.
