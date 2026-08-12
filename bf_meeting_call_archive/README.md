# Meetings ↔ Call archive (`bf_meeting_call_archive`)

An optional bridge between the `bf_meeting` and `bf_sms_archive` modules:
links a meeting report to the matching archived call.

## Features

- A `call_archive_id` field on `meeting.record` (shown when the mode is
  "Phone").
- The reverse `meeting_record_ids` relation on `call.archive.call`, plus a
  banner in the call form.
- `duration_minutes` and `partner_id` are prefilled automatically when a call
  is picked.
- The raw call log is never modified: the promotion is purely declarative.

Auto-installs when `bf_meeting` **and** `bf_sms_archive` are both installed.

## Dependencies

`bf_meeting`, `bf_sms_archive`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
