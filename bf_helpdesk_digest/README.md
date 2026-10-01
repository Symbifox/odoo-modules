# Symbifox Helpdesk: Daily Digest Section

Bridge module between `bf_helpdesk` and `daily_todo_digest`. It adds a
**Helpdesk** section to the morning digest email, so an agent sees their support
work next to their tasks and activities.

It installs automatically when both modules are present.

## What the section shows

For the digest's reader:

- **Their open tickets**, breached SLAs first, then SLAs at risk, then by
  priority, with the client's name and a link to each ticket (up to 12 rows,
  then "and N more"). The title gives the number of open tickets and how many
  are waiting for the client.
- **"Since yesterday"**: the helpdesk notifications the agent chose to receive
  by email, once a day, in `bf_helpdesk`'s notification matrix ("Mes
  notifications").

When the reader has neither open tickets nor pending notifications, the section
is left out.

## How daily notifications are delivered

- An agent who receives the morning digest gets their daily email notifications
  **inside the digest**, instead of a separate email from `bf_helpdesk`. Agents
  who do not receive the digest keep `bf_helpdesk`'s own daily email.
- Items are marked as sent **only once the digest actually goes out**. Rendering
  the digest, for example as a preview, does not consume them.
- **Safety net**: on a day the digest is not sent (nothing else to say), the
  items stay pending, and `bf_helpdesk` sends them by its own email once they
  are 26 hours old. Nothing is lost.

## Configuration

In the digest configuration (`daily_todo_digest`), the **"Assistance (billets et
notifications)"** checkbox turns the section on or off. It is on by default.

The notifications themselves are chosen by each agent in Helpdesk › Mes
notifications (event, channel, timing): pick "email" and "daily digest" to have
them land in the morning digest.

## Dependencies

- `bf_helpdesk` (18.0.4.9.0 or later, for the notification matrix)
- `daily_todo_digest`

## License

LGPL-3. It depends on `bf_helpdesk`, which is AGPL-3 and itself depends on
modules published under the Business Source License 1.1; see their READMEs.

## Changelog

| Version | Change |
|---|---|
| 18.0.1.0.2 | Test only: the 26-hour safety-net test no longer depends on the time of day it runs. |
| 18.0.1.0.1 | Notification items are marked as sent only once the digest actually goes out: a preview of the digest no longer consumes them. |
| 18.0.1.0.0 | First release: Helpdesk section in the morning digest (open tickets of the reader, SLA first, and daily email notifications from the agent notification matrix), with a 26-hour safety net. |
