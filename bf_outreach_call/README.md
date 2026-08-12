# Outreach — logged calls (`bf_outreach_call`)

A bridge between **Outreach campaigns** (`bf_outreach`) and the call archive of
**SMS & call archive** (`bf_sms_archive`).

A scheduled action reconciles the calls held in `call.archive.call` against the targets
of running campaigns, matching on the phone number in international format, and creates
the matching interaction: real direction, real duration, and an outcome derived from the
call type.

This removes the manual logging that usually kills adoption of an outreach tool. The
person makes the call; the campaign records it.

## What each call type becomes

| Call type | Direction | Outcome |
|---|---|---|
| Outgoing | outgoing | *person reached* if it lasted, otherwise *no answer* |
| Incoming | incoming | *person reached* if it lasted, otherwise *no answer* |
| Missed | incoming | *no answer* |
| Voicemail | incoming | *voicemail* |
| Rejected, blocked | — | ignored, they say nothing about the campaign |

A zero duration means nobody picked up, whatever the type says.

## Behaviour

- Matching is on `phone_normalized` (E.164) on both sides, so formatting differences do
  not matter.
- A **date watermark** (`bf_outreach_call.last_scan`) bounds the scan, going back seven
  days at most on a first run.
- Each interaction carries `call_archive_id`, a link back to the archived call. The same
  call is never logged twice.
- Duration is converted from seconds to minutes.

## A note on the softphone

Any softphone that feeds the same call archive is covered by this bridge with no extra
work: a call placed from the browser lands in `call.archive.call`, and the reconciliation
picks it up like any other.

## Installation

`auto_install` is set: the bridge installs itself as soon as both `bf_outreach` and
`bf_sms_archive` are present.

## Dependencies

- `bf_outreach`
- `bf_sms_archive`

## Testing

```bash
odoo -d <db> -u bf_outreach_call --test-enable --test-tags bf_outreach --stop-after-init
```

Covers phone normalisation, the four meaningful call types, the ignored ones, duration
conversion, cadence progression, and the no-duplicate guarantee.
