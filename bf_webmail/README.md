# Webmail from the Odoo systray (`bf_webmail`)

Opens a self-hosted SnappyMail webmail from the Odoo top bar, so mail and ERP
live in one browser tab.

## Why

Odoo's Discuss is not a mailbox. Anyone doing client work keeps a real webmail
open beside Odoo all day and pays the tab-switching tax on every message. If the
webmail is self-hosted anyway, it can simply be framed where the work is.

## What it provides

- A systray entry opening SnappyMail in a full-height frame.
- Instance-level settings: the webmail URL, the IMAP host, and an IMAP password
  stored **encrypted** (Fernet) rather than in plain `ir.config_parameter`.
- Nothing is read or parsed by Odoo: this module frames a webmail, it does not
  implement one.

## Configuration

Settings › General Settings › Blue Fox Webmail:

| Setting | Default | Notes |
|---|---|---|
| Webmail URL | `https://nextcloud.example.com/index.php/apps/snappymail/` | Point at your own SnappyMail |
| IMAP host | `imap.example.com` | Used for the credential handshake |
| IMAP password | — | Encrypted at rest; requires `cryptography` |

The shipped values are placeholders. Set them before use.

## Requirements

Odoo 18 Community, `bf_onboarding_base` (published in this repository), a running
SnappyMail instance, and the `cryptography` Python package for password storage.

## License

LGPL-3.
