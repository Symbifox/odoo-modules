# bf.email inbox — Odoo systray (`bf_email_systray`)

A button in Odoo's systray that opens the `bf.email` inbox, with a counter
(read + unread).

## Features

- Systray icon giving quick access to the `bf.email` inbox.
- Live message counter (read and unread).

## Dependencies

`web`, `bf_email_management`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.

## License

LGPL-3. See [`LICENSE`](LICENSE) for the full text.

**Licence note.** The dependency on `bf_email_management` is BUSL-1.1. There is
no static coupling here — the systray counter reaches `bf.email` through the
ORM at runtime and opens that module's inbox action — so the dependency *could*
be dropped from the manifest. It is kept because doing so would be dishonest
rather than useful: this module is a counter for that inbox and nothing else,
so without it you would get a module that installs and does nothing.

The LGPL-3 text applies to this module's own source, but a working install
needs the BUSL-1.1 terms on `bf_email_management`. If you need more than those
terms allow, [talk to us](https://symbifox.com).
