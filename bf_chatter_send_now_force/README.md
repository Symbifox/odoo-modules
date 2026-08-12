# BF Chatter — Force Send on Scheduled "Send Now" (`bf_chatter_send_now_force`)

Clicking **Send Now** on a scheduled chatter message sends it immediately
instead of waiting up to 5 minutes for the email queue cron.

## Why

Odoo upstream omits `mail_notify_force_send=True` in
`mail.scheduled.message.post_message` (the UI button), so the resulting
`mail.mail` waits for the *Mail: Email Queue Manager* cron. The model's own
daily cron `_post_messages_cron` already injects this flag — this module
restores the same behaviour for the UI button.

## Dependencies

`mail`.

## Licence

Distributed under **LGPL-3**. See the `LICENSE` file.
