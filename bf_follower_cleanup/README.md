# BF — Follower cleanup (`bf_follower_cleanup`)

A cron that removes from chatter followers anyone who is not an internal
employee.

## Features

- A scheduled job running **every 5 minutes**, walking `mail.followers` to
  remove partners not attached to an employee.
- Keeps internal threads clean (avoids leaking notifications to external
  contacts added by accident).

## Who is kept / removed

A follower is **kept** only if it matches an internal user, that is a
`res.users` with `share = False` (active or archived). Every other follower
(external contact, portal/shared user) is removed on the cron's next pass.

### Exempting a partner

The module deliberately maintains **no allowlist** of external contacts to
keep. For a partner to stay subscribed, they must have an internal user account
(`share = False`). Purely external contacts therefore cannot be exempted, and
that is the intended behaviour.

## Configuration parameters

Two system parameters (`ir.config_parameter`) govern the behaviour:

| Key | Default | Purpose |
|-----|---------|---------|
| `bf_follower_cleanup.always_remove_partner_ids` | *(empty)* | Optional list of `res.partner` IDs (separated by `,` or `;`) to purge **unconditionally**, even when attached to an internal user — useful for integration/service accounts. Empty by default. |
| `bf_follower_cleanup.batch_size` | `5000` | Maximum number of follower rows processed per cron pass. |

## Dependencies

`mail`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.

## Changelog

### 18.0.1.0.1

- The default value of `always_remove_partner_ids` is now **empty** (it used to
  be an internal partner ID specific to one deployment).
- Documented the configuration parameters, the cron cadence and the
  always-remove behaviour; added the `LICENSE` file.
