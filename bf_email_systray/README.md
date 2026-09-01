# bf.email inbox — Odoo systray (`bf_email_systray`)

A button in Odoo's systray that opens the `bf.email` inbox, with a counter
(read + unread).

## Features

- Systray icon giving quick access to the `bf.email` inbox.
- Live message counter (read and unread).
- **Two ways to open the inbox**, whichever suits: **as a panel**, anchored
  under the button in the top-right corner, over the current screen which
  stays where it is; or **full page**, the way the menu opens it.
- A menu on the button: open as a panel, full page, or in the list view
  (filters, grouping, pivot, export), plus a choice of what a plain click does.
- The panel is resizable from the grip in its bottom-left corner. Size and
  chosen mode are remembered per person, in the browser.

## Settings

Under **Settings → Email management → Inbox**:

- **What the systray button does**: panel or full page, the database default.
- **Panel width (%)**: starting width, 40 to 100.
- **Panel height (%)**: starting height, 40 to 100.

These are the database defaults. Anyone can override them from the button's
menu, and their choice wins.

## Implementation notes

The panel is mounted through Odoo's `overlay` service at **sequence 40**, below
the sequence dialogs use (50). The overlay container sorts by sequence and all
its items share one z-index, so the wizards the inbox opens itself (routing,
composer, scheduling) always render *above* the panel, never behind it.

It closes on `ACTION_MANAGER:UPDATE`, the event Odoo emits when an action
replaces the page. Without it, "open the linked record" would change the page
underneath a panel that stayed open: `dialog.closeAll()` does not reach the
`overlay` service. The event is not emitted for `target: "new"` actions, so a
dialog does not close the panel.

The counter's domain is a **hand-written copy** of `bf.email._inbox_domain()`.
The badge counts before any action is opened, so it cannot import the server's
domain. Two tests pin the copy, one in this module and one in
`bf_email_management`.

## Dependencies

`web`, `bf_email_management`.

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
