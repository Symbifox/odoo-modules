# BF Email Subject Cleanup

Odoo 18 Community module that prevents `Re:` prefix stacking on subjects of messages sent through the chatter.

## Use case

When an email exchange goes back and forth between Odoo and an external client (Outlook, Gmail, Apple Mail), each reply tacks on a new `Re:` to the subject. After a few round-trips you end up with `Re: Re: Re: Re: Original subject`, which clutters the recipient's inbox and the chatter history. This module always collapses to a single `Re:`, without modifying incoming emails.

## Features

- **Automatic collapse of stacked prefixes** — `Re: Re: Re: Hello` becomes `Re: Hello`
- **Case-insensitive** — `RE: Re: re: Hello` becomes `Re: Hello`
- **BlackBerry/Outlook counter compatibility** — `Re[2]: Hello` and `Re(3): Hello` become `Re: Hello`
- **Tolerant to whitespace and punctuation** — `Re:Hello`, ` Re: Hello`, `Re : Hello` are all normalized
- **No information loss** — subjects without a `Re:` prefix are never touched
- **Applied at compose and post** — the user sees the clean subject in the wizard immediately; the stored `mail.message` and the outbound email are clean
- **Incoming subjects preserved** — IMAP gateway and `message_parse` are not affected; the original `Re:` chain is kept as received
- **No external dependencies** — only Python's standard `re` library, no extra package

## Technical architecture

### Structure

```
bf_mail_subject_clean/
+-- __init__.py
+-- __manifest__.py
+-- README.md
+-- models/
    +-- __init__.py
    +-- common.py                    # helper normalize_reply_subject()
    +-- mail_compose_message.py      # override _compute_subject (UX wizard)
    +-- mail_thread.py               # override message_post (inline chatter + RPC + Python)
```

### Dependencies

| Module | Role |
|--------|------|
| `mail` | Provides `mail.thread.message_post` and the `mail.compose.message` wizard |
| `bf_onboarding_base` | Shared onboarding-panel helpers (guided setup step) |

### Normalization helper

```python
_REPLY_PREFIX_RE = re.compile(
    r'^(?:\s*re(?:\s*[\[(]\d+[\])])?\s*:\s*)+',
    re.IGNORECASE,
)

def normalize_reply_subject(subject):
    if not subject or not isinstance(subject, str):
        return subject
    match = _REPLY_PREFIX_RE.match(subject)
    if not match:
        return subject
    return f'Re: {subject[match.end():]}'
```

The regex is designed to resist catastrophic backtracking: the `:` required at each iteration prevents any pathological sequence from blowing up. Measured at <1 ms on a 20,000-character worst case.

### Override points

| Model | Method | Effect |
|-------|--------|--------|
| `mail.compose.message` | `_compute_subject` | Cleans the subject pre-filled from the parent when the user opens the full-screen composer |
| `mail.thread` | `message_post` | Cleans the `subject` kwarg before posting, which covers: inline chatter, RPC `message_post`, business Python code, third-party modules calling `message_post` |

The `message_post` override is applied to the abstract `mail.thread` model and is therefore active on every model that inherits it (tasks, partners, invoices, projects, helpdesk tickets, etc.).

### Security

- No new model, no new table, no new `ir.model.access.csv`
- No `sudo()` call, no privilege escalation
- No network resource, no secret, no external resource
- Pure string manipulation — no SQL or XSS injection possible

### Cases covered

| Input | Output |
|-------|--------|
| `Re: Re: Re: Hello` | `Re: Hello` |
| `RE: Re: re: Hello` | `Re: Hello` |
| `Re[2]: Hello` | `Re: Hello` |
| `Re(3): Hello` | `Re: Hello` |
| `Re:Hello` (no space) | `Re: Hello` |
| ` Re: Hello` (leading space) | `Re: Hello` |
| `Re: Hello` | `Re: Hello` (unchanged) |
| `Hello` | `Hello` (unchanged) |
| `Replied: but not really` | `Replied: but not really` (unchanged) |
| `Fw: Re: Hello` | `Fw: Re: Hello` (unchanged — the module doesn't touch `Fw:`/`Tr:`) |
| `""` or `None` | as-is |

## Installation

```bash
docker compose exec odoo odoo -d <database> -i bf_mail_subject_clean --stop-after-init
```

Or from the UI: **Apps** → refresh the list → install **BF Email Subject Cleanup**.

## Uninstall

No data migration required — no data is created by this module. Uninstall via the UI or CLI:

```bash
docker compose exec odoo odoo -d <database> --stop-after-init -- shell -c "self.env['ir.module.module'].search([('name','=','bf_mail_subject_clean')]).button_immediate_uninstall()"
```

## License

LGPL-3

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
