# Outreach — inbound email replies (`bf_outreach_email`)

A bridge between **Outreach campaigns** (`bf_outreach`) and **Email management**
(`bf_email_management`).

A scheduled action goes through the **received** emails archived in `bf.email` and,
when the sender matches the address of an active target, creates the matching inbound
interaction.

The practical effect: the campaign option *stop at the first reply* no longer depends on
anyone remembering to log the reply. A target who answers leaves the cadence on its own
and shows up under the "replied" filter.

## Behaviour

- Matching is done on the **normalised** sender address, so casing and display names do
  not matter.
- A **date watermark** (`ir.config_parameter` → `bf_outreach_email.last_scan`) bounds the
  scan. On a first run it goes back seven days at most, so installing the bridge never
  replays years of archives onto a campaign that has just started.
- Each interaction carries `bf_email_id`, a link back to the archived email. That link is
  also the safeguard: the same email is never matched twice, even if the watermark is
  reset.
- Only targets whose campaign is running or draft, and whose stage is not `won` or
  `lost`, are considered.

## Installation

`auto_install` is set: the bridge installs itself as soon as both `bf_outreach` and
`bf_email_management` are present. There is nothing to configure.

## Dependencies

- `bf_outreach`
- `bf_email_management`

## Testing

```bash
odoo -d <db> -u bf_outreach_email --test-enable --test-tags bf_outreach --stop-after-init
```

Covers the match itself, the freeze of the cadence, the refusal to match an unknown
sender or a closed target, the watermark bound, and the guarantee that a second pass
creates nothing.
