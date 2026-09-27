# Changelog — Symbifox Color (`bf_color`)

## 18.0.1.0.1 — 2026-09-26

- Security: the entries of a shared company swatch could be changed or deleted
  by any internal user. They now follow their swatch: read by everyone who can
  read the swatch, changed by its owner, or by an administrator for a shared
  swatch.

## 18.0.1.0.0 — 2026-09-26

- First release: free hex colors resolved per user, per company, by automatic
  rules and per record; closest Odoo palette index and readable text color;
  personal and shared swatches; color picker; contact tags wired.
