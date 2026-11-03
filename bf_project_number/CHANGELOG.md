# Changelog

## 18.0.1.1.0 — 2026-11-03

Source strings moved to English, with a complete `fr_CA.po` reproducing the
original French word for word. Module icon added (already shipped to the
first tenant under 1.0.0). No behaviour change.

## 18.0.1.0.0 — 2026-11-02

First version. Three-digit project number on `project.project`, assigned at
creation to the smallest free number, editable by hand as long as the target
number is free, unique database-wide through a Postgres index. Existing
projects are numbered at install. Reads in the list (right of the favorite
star), as a kanban badge, on the form and in the search bar.

Born from the need for a counter that matches hand-kept project books
("a project number, an ordering customer, a beneficiary client and a budget")
instead of restarting from Odoo's technical `id`.
