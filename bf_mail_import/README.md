# BF Email Import (.eml)

Odoo 18 Community module for importing `.eml` files (RFC 2822) directly into the chatter of any record.

## Use case

When emails are received or sent outside of Odoo (external mail client, webmail, forwarded between colleagues), there is no native way to attach them to an existing thread. This module fills that gap by adding an `.eml` import button to the chatter.

## Features

- **`.eml` button in the chatter** — visible on any record inheriting `mail.thread`
- **Multi-file import** — upload multiple `.eml` files in a single operation
- **Direct import** — single click to import (no preview step)
- **Chronological order** — files are pre-sorted by send date before import, guaranteeing chronological display in the chatter
- **Extension validation** — only `.eml` files are accepted
- **Duplicate detection** — RFC 2822 `Message-ID` is checked before insertion
- **Automatic author resolution** — looks up the `res.partner` matching the sender's email address
- **Threading preservation** — `parent_id` resolved through `In-Reply-To` / `References` headers, with validation that the parent belongs to the same thread
- **Zero notifications** — import does not trigger any outbound email or auto-subscription
- **Inline attachments only** — only attachments contained in the email are imported (no duplication of the original `.eml`)
- **Detailed report** — the import summary lists each file individually (imported, duplicate skipped, error)

## Technical architecture

### Structure

```
bf_mail_import/
+-- __init__.py
+-- __manifest__.py
+-- README.md
+-- security/
|   +-- ir.model.access.csv
+-- wizard/
|   +-- __init__.py
|   +-- mail_import_wizard.py
|   +-- mail_import_wizard_views.xml
+-- static/
    +-- src/
        +-- js/
        |   +-- chatter_import_patch.js
        +-- xml/
            +-- chatter_import_patch.xml
```

### Dependencies

| Module | Role |
|--------|------|
| `mail` | Sole dependency — provides `mail.thread`, `message_parse()`, `message_post()` |

No external dependencies, no additional Python libraries.

### Email parsing

The module delegates **100% of RFC 2822 parsing** to the standard chain:

1. `email.message_from_bytes(raw, policy=email.policy.default)` — produces an `EmailMessage` (modern Python 3 API, required by Odoo 18)
2. `self.env['mail.thread'].message_parse(email_msg, save_original=False)` — public `@api.model` Odoo method

The dict returned by `message_parse` contains: `message_id`, `subject`, `email_from`, `to`, `cc`, `body`, `date`, `parent_id`, `partner_ids`, `attachments`, `references`, `in_reply_to`, etc.

### Wizard (`bf.mail.import.wizard`)

Two-state `TransientModel`:

| State | User action | Behavior |
|-------|-------------|----------|
| `draft` | Select `.eml` files + click Import | `many2many_binary` widget linked to `ir.attachment`, direct import |
| `done` | Result displayed | Detailed per-file summary: imported (+), duplicates (-), errors (!) |

**Three-phase import pipeline:**

1. **Parse** — extension validation + parsing of each file via `message_parse()`
2. **Sort** — sort by ascending send date (the chatter displays by `id DESC`, so auto-incremented IDs reflect chronological order)
3. **Import** — message creation via `message_post()` in sorted order

**`message_post` call:**

```python
target.with_context(
    mail_create_nosubscribe=True,      # no auto-subscription
    mail_create_nolog=True,            # no creation log
    mail_notify_force_send=False,      # no immediate send
    mail_auto_subscribe_no_notify=True,# no notification to followers
    tracking_disable=True,             # no field tracking
).message_post(
    body=Markup(body_html),
    message_type='email',              # "email" display in the chatter
    subtype_xmlid='mail.mt_comment',
    message_id=rfc2822_message_id,     # via **kwargs -> mail.message column
    date=original_date,                # via **kwargs -> mail.message column
    ...
)
```

### OWL patch (chatter)

The button is injected via the standard Odoo 18 patch pattern:

- **JS**: `patch(Chatter.prototype, {...})` adds the `onClickImportEml()` method
- **XML**: Template `t-inherit="mail.Chatter"` with xpath after the "Activities" button
- **Refresh**: `this.load(this.state.thread, ["messages"])` after the wizard closes

### Security

- CRUD access to the wizard for all internal users (`base.group_user`)
- Technical "Import .eml" menu reserved for administrators (`base.group_system`)
- Real access control is the target record's — `message_post` checks write rights

### Edge case handling

| Case | Behavior |
|------|----------|
| Extension other than `.eml` | `UserError` with file name, added to errors, other files continue |
| Corrupt file | Error caught, added to summary, other files continue |
| `Message-ID` already present in `mail.message` | File skipped, listed under duplicates |
| `parent_id` from another thread | Silently ignored (set to `False`) to avoid cross-thread links |
| Sender without `res.partner` | `email_from` displayed as-is in the chatter (Odoo native behavior) |
| `.eml` without body | Message posted with empty body, subject and attachments preserved |
| Non-UTF-8 encoding | Handled by `email.message_from_bytes()` + `message_parse()` |
| Target record deleted | `UserError` before import attempt |
| Model without `mail.thread` | `UserError` in `default_get()` |

## Installation

```bash
docker compose exec odoo odoo -d <database> -u bf_mail_import --stop-after-init
```

## Usage

1. Open any record with a chatter (project, task, partner, invoice, ticket, etc.)
2. Click the **`.eml`** button in the chatter bar (next to "Activities")
3. Upload one or more `.eml` files
4. **Import** — the messages appear in the chatter with the original date and sender

## Changelog

### 18.0.1.3.1
- **`.eml` only**: removed `.msg` from the accepted extension whitelist. Outlook `.msg` files use the OLE compound-document format, which `email.message_from_bytes()` (RFC 2822) cannot parse — they previously passed the extension gate and then failed during parsing. README and validation now consistently state `.eml` only.

## License

LGPL-3

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
