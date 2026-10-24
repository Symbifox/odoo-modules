# Company Voice for Automatic Notices

Automatic notices speak for the company, never as « Public user » or a bot.

## What it changes

| Before | After |
|---|---|
| A document signed on the public page: « Public user » posts « Document signed » in the chatter and authors the e-mail | The company's contact |
| A backup report, a date-poll registration, a booking: « Public user » in the chatter | The company's contact |
| « Public user has just assigned you the following activity » | « Your Company has just assigned you… » |
| Hour-bank status, daily digest, secure transfer: sender `info@example.com` with no name | `"Your Company" <info@example.com>` |
| A notice sent by a scheduled action: `"OdooBot" <info@example.com>` | `"Your Company" <info@example.com>` |

## What it leaves alone

- A comment a visitor typed through a portal link keeps its author: a stranger's text never goes out under the company's name.
- An incoming e-mail keeps the header it arrived with.
- A name someone chose on the company's address (a newsletter sent under a person's name, a team signature such as « Company — Support ») is kept.
- Discuss channels, live-chat guests, portal users and internal users.
- The sender's address never changes: only the display name does.

## Settings

System parameter `bf_mail_company_voice.company_voice`:

- `record` (default): the company of the record speaks (multi-company).
- `session`: the session's company speaks for every record.
- a company id (e.g. `3`): that one company always speaks, for a database whose public user or scheduled actions belong to a second company that must not appear in notices.

## Technical notes

Only `mail.message.create` is extended (`mail.mail` goes through it by delegation). The author is replaced on system message types only (`notification`, `email_outgoing`, `user_notification`, `auto_comment`). A failure in the renaming is logged and never blocks the message.

## Requirements

- Odoo 18.0 (Community)
- `mail` (core). No other dependency.

## Licence

LGPL-3. See `LICENSE`.
