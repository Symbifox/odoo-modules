# Outreach — bookings (`bf_outreach_appointment`)

A bridge between **Outreach campaigns** (`bf_outreach`) and **Appointments**
(`bf_appointment`, on top of OCA's `resource_booking`).

When a booking reaches *scheduled* or *confirmed* for the contact behind an outreach
target, the module:

- logs a **meeting** interaction on that target, carrying the booking's real date and
  duration;
- moves the target to the **meeting booked** stage.

The outcome you were chasing records itself, and the file stops appearing in the
follow-up lists.

## The booking link

A campaign can carry a **booking type**. Its public URL is then exposed on every target
of that campaign as `booking_url`, ready to be dropped into the campaign's email
template. The target picks their own slot, and the booking that follows moves their file
along without anyone touching it.

## Behaviour

- Only targets whose campaign is running or draft, and whose stage is not `won` or
  `lost`, are considered.
- Each interaction carries `booking_id`, a link back to the booking. Editing a booking
  afterwards never duplicates the interaction.
- The reconciliation is best-effort: a failure is logged and swallowed, because a
  convenience must never make a client's booking fail.

## Installation

`auto_install` is set: the bridge installs itself as soon as both `bf_outreach` and
`bf_appointment` are present.

## Dependencies

- `bf_outreach`
- `bf_appointment`

## Testing

```bash
odoo -d <db> -u bf_outreach_appointment --test-enable --test-tags bf_outreach --stop-after-init
```

Covers the interaction and the stage change, the no-duplicate guarantee on edit, the
refusal to touch an unrelated contact or a closed target, and the booking URL exposed
from the campaign.
