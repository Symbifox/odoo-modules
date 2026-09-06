# Forge

Field builder for Odoo 18 Community. Add custom fields, related/computed
fields, and inject them into existing views without writing a module — and
have the customizations survive `-u all` upgrades.

## Why

Odoo Enterprise ships **Studio**; Community does not, and OCA has no
direct equivalent. Asking a developer for every new field on the contact
form, the lead form, the task form, etc. is slow and expensive. Studio
Light closes that gap for the simple-and-most-frequent case: *"add a
field on the contact form, show it in the list, expose it in search."*

It is **not** a replacement for Studio Enterprise's full feature set.
By design it stays narrow.

## What it does

### Field builder

- Wizard to add a custom field of any common type: char, text, html,
  integer, float, monetary, boolean, date, datetime, selection, many2one,
  binary (attachment / file), image (thumbnail-rendered binary).
- **Conditional modifiers** (since v18.0.6.0): set `invisible_expr`,
  `required_expr`, or `readonly_expr` on the field. Expressions are
  Python-style (e.g. `state == 'draft'`) and pass an AST whitelist
  that refuses function calls, subscripts, comprehensions, lambdas,
  imports — keeping the eval surface narrow.
- Auto-injection in the form view at a chosen anchor (after an existing
  field, in a tab, in a new group).
- Optional injection in **list / kanban / search** views.
- **Survives `-u all`**: metadata persisted in `studio.light.field` and
  `studio.light.view.injection`. A post-init hook and a daily integrity
  cron recreate the underlying `ir.model.fields` and `ir.ui.view`
  records if they are lost during a parent-module reinstall. PostgreSQL
  column data is preserved — only Odoo metadata is rebuilt.
- Console UI under the *Customization* menu.

### Related fields

Tick *Computed from another field* and provide a dotted path
(e.g. `partner_id.country_id.name`). The field is created as a
read-only related field — no Python compute method generated.
Sensitive field names (passwords, tokens, secrets) are denied and
locked models cannot be traversed.

### Default values & shared filters

Friendly menus that wrap `ir.default` and `ir.filters` so admins
can set per-user / company-wide defaults and shared filters without
diving into the Technical menu.

### Smart buttons (since v18.0.4.0.0)

Wizard to add a smart button (the count badges at the top of a form,
e.g. *3 Tasks*) without writing code:

- Pick a *source* model (where the button appears) and a *target*
  model (whose related records you count).
- Pick the Many2one field on the target that points back to the source.
- Optionally narrow the count with a `domain` (Python-literal list of
  3-tuples; only literal values, no expressions).
- Pick a label, FontAwesome icon, color, and whether clicking opens
  the related list.

The count is fetched at runtime from a JSON controller endpoint
(`/studio_light/smart_button/count`). **No `compute=` Python is ever
generated** and no server actions are constructed from user input —
the click action is a static `ir.actions.act_window` dict assembled
server-side from already-validated parts.

## What it does NOT do

- No new full models (only fields on existing models).
- No QWeb / report editor.
- No drag-and-drop full-page form designer.
- No `compute=` Python code generation (RCE risk).
- No selection extension on core (non-manual) fields — Odoo blocks
  `ir.model.fields.selection.create()` on non-manual fields by design.
- No JSONB translation inline editor.
- Smart buttons cannot reuse existing `ir.actions.act_window` records
  (intentional — those can carry server-side `code` chains).

## Security model

This module exposes admin-level capabilities. Two groups are involved:

| Group                                      | Capability                                                      |
| ------------------------------------------ | --------------------------------------------------------------- |
| **Forge: Administrator**            | Create fields and view injections on non-locked models.         |
| **Forge: Bypass model lock**        | (Sysadmin only) Allow operating on locked models.               |

**Locked models** (refused by default): `ir.*`, `base.*`, `bus.*`,
`mail.*`, `account.*`, `payment.*`, `auth.*`, `auth_*`,
`res.config.*`, plus exact entries for `res.users`, `res.groups`,
`ir.config_parameter`, `ir.attachment`, `ir.cron`, `ir.module.module`,
`ir.actions.*`, `ir.rule`, `ir.model.access`, and the module's own
models.

**Sensitive field denylist** for related paths: `password`,
`password_crypt`, `api_key`, `secret`, `client_secret`, `private_key`,
`access_token`, `refresh_token`, `totp_secret`, `smtp_pass`,
`webhook_secret`, etc.

**Arch-snippet whitelist**: only `field`, `group`, `notebook`,
`page`, `separator`, `label`, `div`, `span`, `newline`, `filter`,
`searchpanel` are allowed in arch snippets. `<button>`, `<header>`,
and `t-*` directives are refused.

**XPath restrictions**: custom xpath cannot use logical operators
(`or`, `and`), the union operator (`|`), wildcard predicates
(`*[`, `//*`), or axis selectors (`::`).

See `SECURITY_AUDIT.md` for the full audit log and threat model.

## Installation

Drop the module into your `addons_path`, refresh the apps list, and
install via *Apps*.

The post-init hook runs the integrity check on first install and on
every `-u bf_studio_light`.

## Usage

1. Go to *Customization > Add custom field*.
2. Pick model (e.g. *Contact*), label, type, target field on the
   form view.
3. Optionally tick *list / kanban / search* and *Read from another
   field*.
4. Save. The field is created on the model, the views are patched,
   and the record lands in *Customization > Custom fields*.

## Architecture

| Concern                                  | Where                                                 |
| ---------------------------------------- | ----------------------------------------------------- |
| Field metadata                           | `studio.light.field` + `studio.light.field.selection` |
| View inheritance metadata                | `studio.light.view.injection`                         |
| Smart-button definition                  | `studio.light.smart.button`                           |
| Smart-button count endpoint              | `controllers/main.py` → `/studio_light/smart_button/count` |
| Smart-button OWL widget                  | `static/src/js/smart_button_widget.js`                |
| Wizard                                   | `studio.light.wizard` (transient)                     |
| Smart-button wizard                      | `studio.light.smart.button.wizard` (transient)        |
| Field provisioning                       | `_ensure_ir_model_field`                              |
| View provisioning                        | `_ensure_ir_view`                                     |
| Smart-button provisioning                | `_ensure_provisioned` on `studio.light.smart.button`  |
| Survival hook                            | `post_init_hook` in `hooks.py`                        |
| Daily integrity cron                     | `data/studio_light_data.xml`                          |
| Locked models                            | `models/studio_light_field.py:LOCKED_*`               |

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-26, this version converts automatically to
  **LGPL-3.0-or-later**.

Versions up to and including 18.0.6.1.0 were released under LGPL-3 and stay
LGPL-3 for whoever holds a copy. BUSL-1.1 applies from 18.0.7.0.0 onward.
