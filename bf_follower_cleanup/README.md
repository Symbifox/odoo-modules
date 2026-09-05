# BF — Notification hygiene (`bf_follower_cleanup`)

Keeps internal notifications internal: a cron that removes from chatter
followers anyone who is not an internal employee, and a guard that never sends
an activity notification to a portal account.

## Features

- A scheduled job running **every 5 minutes**, walking `mail.followers` to
  remove partners not attached to an employee.
- Keeps internal threads clean (avoids leaking notifications to external
  contacts added by accident).
- **Unsubscribing configured users from the leads they do not sell.** Odoo
  subscribes people to a lead as a side effect of ordinary work, so a single
  activity is enough to follow it forever. A second cron unsubscribes the
  listed users from every lead whose salesperson is not them, and only ever
  unlinks.
- **An activity assigned to a portal user no longer emails that user.** Odoo
  notifies the assignee of an activity as a side effect of `create()` and of a
  reassignment; its only native guard is "the assignee is not the writer", and
  nothing checks that the assignee is an internal user. Any module scheduling
  an activity on a record whose responsible happens to be a portal contact
  therefore sends that contact an internal reminder they cannot even open — a
  portal user has no activity view. The activity itself is untouched and stays
  visible internally; only the notification to a `share` account is dropped,
  and dropping it is logged.

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

Four system parameters (`ir.config_parameter`) govern the behaviour:

| Key | Default | Purpose |
|-----|---------|---------|
| `bf_follower_cleanup.always_remove_partner_ids` | *(empty)* | Optional list of `res.partner` IDs (separated by `,` or `;`) to purge **unconditionally**, even when attached to an internal user — useful for integration/service accounts. Empty by default. |
| `bf_follower_cleanup.batch_size` | `5000` | Maximum number of follower rows processed per cron pass. |
| `bf_follower_cleanup.crm_lead_unfollow_user_ids` | *(empty)* | `res.users` IDs to unsubscribe from the leads they do not sell. Empty by default, so a system that never opted in is untouched. |
| `bf_follower_cleanup.crm_lead_keep_ids` | *(empty)* | `crm.lead` IDs those users stay subscribed to whoever the salesperson is. |

## Dependencies

`mail`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.

## Changelog

### 18.0.2.1.0

- **NEW:** `mail.activity.action_notify` no longer emails an assignee whose
  user account is a `share` (portal) one. Odoo's own guard only checks that
  the assignee is not the writer, so any module scheduling an activity on a
  record owned by a portal contact was mailing that contact an internal
  reminder they cannot open. Measured in production: six such emails to one
  portal contact over four and a half months, all from a single follow-up
  cron. The activity stays; only the outgoing notification is dropped, and it
  is logged with the accounts concerned.
- **FIX:** the module no longer ships a partner ID belonging to another
  deployment as the default of `always_remove_partner_ids`. The 18.0.1.0.1
  entry below announced that default as emptied; the data file still carried
  the ID, so a fresh install unconditionally purged whatever partner happened
  to hold that ID. The seed is now empty, as documented. An existing
  installation is unaffected — the record is `noupdate`, so its current value
  stays whatever it was set to.

### 18.0.2.0.0

- **NEW:** A second cron unsubscribes configured users from the leads they do
  not sell (`crm_lead_unfollow_user_ids`, with a `crm_lead_keep_ids`
  exception list). A lead with no salesperson counts as not theirs, an open
  activity assigned to the user shields the lead, and the pass only ever
  unlinks.

### 18.0.1.0.1

- The default value of `always_remove_partner_ids` is now **empty** (it used to
  be an internal partner ID specific to one deployment).
- Documented the configuration parameters, the cron cadence and the
  always-remove behaviour; added the `LICENSE` file.
