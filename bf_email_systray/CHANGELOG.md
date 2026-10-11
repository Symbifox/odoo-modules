# Changelog

## [18.0.2.2.1] - 2026-10-11

### Changed

- **The Emails button no longer holds the web client back.** Odoo 18 mounts
  the web client only once every component has finished its `onWillStart`,
  and the button waited there for its configuration call. The call now runs
  without being awaited: the mode remembered by the browser applies at once,
  the database configuration follows, and the remembered mode still has the
  last word.

## [18.0.2.2.0] - 2026-10-08

### Added

- **A contact's emails in the panel.** The "Emails" button of a contact form
  opens the panel over the form, filtered on that contact; the panel title
  names the contact until the filter is removed. In "page" mode, the full
  page opens, filtered.

