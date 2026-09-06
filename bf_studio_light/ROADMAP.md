# Roadmap — closing the gap with Odoo Studio (Enterprise)

> Companion to `README.md` and `SECURITY_AUDIT.md`. The README answers
> *"what does this do today?"*, the audit answers *"why is it safe?"*,
> and this file answers *"what would it take to make it a real Studio?"*
>
> Origin: an internal request — *"Make a real 'Studio'. What is the gap? To
> make it truly fully featured."*

## 1. Method

"Fully featured" here means **functional parity with Odoo Studio
Enterprise as shipped in Odoo 18**, minus the bits that cannot be
delivered safely in a Community module (see §2-C).

Every item in the audit below is benched against three policy bright
lines that the v0.x line has already established. These are
non-negotiable; any future tier that would cross one is rejected, not
flagged as future work.

1. **No Python codegen** — we never write into `compute=`,
   `ir.actions.server.code`, or `ir.actions.report.code` from
   user-supplied input. That door is RCE; it stays closed.
2. **No silent escalation** past locked models or the sensitive-field
   denylist. Bypass exists, but only via the explicit
   `group_studio_light_unlocked` group — never via a context flag, an
   `sudo()`, or a "trusted source" inference.
3. **No reuse of pre-existing `ir.actions.*` records** as click
   targets for buttons or automations. The click action is always
   assembled server-side from already-validated parts. Reuse would
   re-open the server-action `code` chain.

These three constraints are what make Studio Light *Studio Light*
rather than Studio. They also mean a small handful of Studio
Enterprise features will never have an equivalent here. That's a
feature, not a bug — see §5.

## 2. Gap audit

Items are bucketed by *feasibility*, not by *desirability*. Order
within a bucket roughly tracks dependency.

### A. Safely buildable — extensions of existing patterns

These all reuse the v18.0.4 architecture (wizard → validated record →
`_ensure_*` provisioner → survival via post-init + cron). None of
them require new policy. Estimates assume an experienced Odoo dev
familiar with the existing module.

| # | Feature | Notes |
| - | --- | --- |
| A1 | **Binary / Image fields** | Add `binary` and `image` entries to `SUPPORTED_TYPES`; rely on existing Odoo upload widgets. Caveat: `ir.attachment` is locked, so binaries live as inline blobs on the host record, not as attachments. |
| A2 | **Many2many fields** | Mirror the m2o controls + apply the same locked-model rule on the target. Choice of widget (`many2many_tags`, `many2many_checkboxes`) exposed in the wizard. |
| A3 | **Reference fields** | High blast-radius because `model_id` is user-chosen. Require a per-field **whitelist of allowable target models** at creation, locked against the same denylist used for related-field traversal. |
| A4 | **Properties fields** (Odoo 17+ native) | Wrap `fields.Properties` so admins can attach a parent record (e.g. *Properties of Project on Task*) and define the property schema through the wizard. No codegen — it's a native field type. |
| A5 | **One2many fields** | Reciprocal of A2/m2o. Useful paired with smart buttons: define the m2o on B → expose the o2m on A. |
| A6 | **Field rename + archive workflow** | Currently you can create but not rename or retire a field cleanly. Add a wizard that renames the `ir.model.fields` record + rewrites every `studio.light.view.injection` that references it. Archive flow flips `state` and removes the injection without dropping the column. |
| A7 | **Conditional modifiers** (`invisible` / `required` / `readonly`) | Domain-style expressions only — `[('field','=','value')]` — parsed and validated server-side; no Python `eval`. |
| A8 | **Declarative computed fields** | Restricted expression language: `sum_of(o2m_field.amount)`, `count_of(o2m_field)`, `concat(a, b)`, basic math. AST-walk the parsed expression against a whitelist of nodes/functions. No `compute=` string written. Falls back to the related-field machinery where possible. |
| A9 | **Dynamic defaults from a whitelisted menu** | Today's `ir.default` wrapper accepts literal values only. Add a dropdown of pre-vetted expressions: `=context_today()`, `=uid`, `=company_id`, `=now()`, etc. Each expression maps to a hard-coded server-side handler — no string `eval`. |
| A10 | **Kanban card decorator pack** | Templated arch snippets for the common decorations: avatar, ribbon, color-coded status tags. Selected by checkbox in the wizard; generated arch goes through the existing whitelist. |

### B. Major builds — feasible but non-trivial

These either introduce a new architectural pillar (export, model
builder, automation wrapper) or require relaxing a current
restriction in a tightly-scoped way.

| # | Feature | Notes |
| - | --- | --- |
| B1 | **Studio Customizations export** ("Extract to module") | Wizard that reads every `studio.light.field`, `studio.light.view.injection`, `studio.light.smart.button` on a given model (or all) and emits a real installable `.zip`: `__manifest__.py`, `models.py`, `views.xml`, `security/ir.model.access.csv`. This is the **portability unlock** — once it exists, anything built in Studio Light can be promoted to a versioned, code-reviewable module. Should land before B2 because B2's output also benefits from being exportable. |
| B2 | **New full models** | `studio.light.model` table → creates a new `models.Model` at install/upgrade time via `_inherit = 'ir.model'` machinery (similar to how Studio's `studio_customization` module works). Auto-creates default form/list/kanban views and a baseline ACL row. Locked-model denylist still applies — you can't create a model named `account.invoice2`. |
| B3 | **New apps / menus** | Builds on B2: pick icon (from a curated FontAwesome + SVG pack), color, parent menu (or top-level). Generates `ir.ui.menu` + `ir.actions.act_window` records, never `ir.module.module` rows. |
| B4 | **Statusbar / stage editor** | Currently `<header>` and `<statusbar>` are blacklisted from the arch whitelist (S12 in the security audit). Introduce a **constrained variant**: a wizard that emits exactly one `<header><field name="..." widget="statusbar"/></header>` block, fully server-templated, with no user-supplied attributes other than the field name. The wizard validates the target field is a `selection` or `many2one` on the host model. |
| B5 | **JSONB inline translation editor** | Tier 2.7 in the handoff (deferred). The blocker is per-record ACL: a translation editor must filter by what the user is allowed to read on the host record, not just by `lang`. Build it as a side-panel that calls `update_field_translations` only after re-checking model + record ACLs. Heavy testing surface. |
| B6 | **No-code automations** (wrap `base_automation`) | Add `base_automation` to `depends`. Wizard exposes: trigger (`on_create`, `on_write` of field X, `on_unlink`, `on_time` scheduled), action set restricted to **declarative actions only** — post message / create activity / send email (templated) / set field to a literal or whitelisted expression. **No `code=` action exposed**, ever, even via the unlocked group. |
| B7 | **Email template builder** | Simplified `mail.template` UI tied to B6. Body is a sandboxed subset of QWeb — `<t t-out="object.field"/>` only, no `t-if`/`t-foreach`/`t-call`/`t-esc`. Backed by the same identifier-regex pattern already used for `target_field`. |

### C. Hard / blocked / out of scope

These either have a poor risk/reward ratio (C1, C2) or are
architecturally blocked by core Odoo or by §1's bright lines (C3–C5).
Listing them explicitly so future contributors don't repeatedly
re-litigate the question.

| # | Feature | Verdict | Why |
| - | --- | --- | --- |
| C1 | **QWeb PDF report editor** | Permanent out-of-scope | Studio's marquee feature, single biggest engineering line item. Building a safe QWeb editor (templating, sandbox, preview pipeline, asset bundling) is comparable in effort to the rest of this roadmap combined. Recommend customers needing PDF customization either pay for Studio or have a dev write the QWeb directly. |
| C2 | **Full drag-and-drop OWL view designer** | Defer indefinitely | The JS effort approaches Studio itself. The wizard-driven UX in Studio Light is intentionally less flashy but covers 80% of the daily ask. Revisit only if a customer specifically pays for it. |
| C3 | **Selection extension on core fields** | Blocked by Odoo core | `ir.model.fields.selection.create()` refuses non-manual fields with *"Properties of base fields cannot be altered in this manner!"*. Tier 2.3 was abandoned in v18.0.3 for exactly this reason. No safe workaround. |
| C4 | **Python `compute=` / `code=` server actions** | Blocked by §1.1 | Direct RCE. Not negotiable. A86 (declarative computed) and B6 (declarative automations) cover the legitimate use cases. |
| C5 | **`ir.rule` / record-rule editor** | Deferred behind a new dedicated group | A single bad rule opens the whole DB to every authenticated user. Possible eventually, but only behind a separate `group_studio_light_acl_editor` group with the same opt-in posture as `group_studio_light_unlocked`. Until then, ACL changes go through a developer. |

## 3. Recommended sequencing

Tiers are ordered to maximise *unlock value per week*: extracting to a
real module (B1) ranks high because every tier after it benefits from
exportability; new-model-and-app (B2/B3) is the largest single item
and deliberately lands late.

| Version | Theme | Items | Rough effort |
| --- | --- | --- | --- |
| **v18.0.5.0** | Field-type expansion + cosmetics | A1, A2, A3, A5, A6 + `static/description/icon.png` + `i18n/fr_CA.po` | 1–2 weeks |
| **v18.0.6.0** ✅ *(A7 shipped 2026-05-11)* | Conditional modifiers | A7 only (`invisible_expr` / `required_expr` / `readonly_expr` with AST-whitelist validator) | actual: same day |
| **v18.0.6.1** *(deferred)* | Remaining smarter-fields | A4 (properties — needs parent `properties_definition` bootstrap), A8 (declarative compute via `make_compute` + structured operations), A9 (dynamic defaults — `ir.model.fields.default` isn't auto-applied to manual fields so needs a `_register_hook` override) | ~2 weeks |
| **v18.0.7.0** | Portability + kanban polish | **B1** (extract-to-module) + A10 | ~2 weeks |
| **v18.0.8.0** | No-code automations | B6 + B7 | 2–3 weeks |
| **v18.0.9.0** | Workflow surface | B4 (statusbar editor) + B5 (translation inline) | ~2 weeks |
| **v18.1.0.0** | New models + new apps | B2 + B3 | 3–4 weeks (major) |
| **vNext (R&D, not committed)** | Pick *one*: report editor *or* drag-drop designer | C1 *or* C2, never both | ≥ 4 weeks each |

Each tier ends with a green `--test-tags /bf_studio_light` run on
`odoo-staging` and a publish-readiness re-check (no `__pycache__`,
no local paths in docs, no internal warnings).

## 4. Test & quality budget

The v18.0.4 line ships **40/40** passing tests. Future tiers inherit
that bar:

- ≥ 80% coverage on new wizards and lifecycle methods
- One `tests/test_security.py` block per new attack surface, regardless
  of whether the surface looks innocuous (the smart-button JSON
  endpoint *also* looked innocuous and uncovered a `statement_timeout`
  oversight in review)
- `--test-tags /bf_studio_light` is the gate before any tag bump in
  `__manifest__.py`
- New `depends` (`base_automation` in B6) require re-running the full
  suite, since survival hooks interact with cron + post-init

## 5. What we won't do, and why

Stated up front so contributors don't keep asking:

- **No PDF/QWeb report editor.** Effort vs. value is bad; the
  workaround (have a dev write QWeb) is already cheap. See C1.
- **No drag-and-drop view designer.** Wizard-driven UX is a
  deliberate choice; it constrains the surface area and avoids the
  Studio-grade JS investment. See C2.
- **No Python codegen, ever.** The whole module's value
  proposition is "Studio without the RCE risk". The day we let user
  input become Python is the day we delete this module. See §1.1.
- **No core-field selection extension.** Odoo core refuses it; we
  don't have a non-RCE path to it. See C3.
- **No record-rule editor in the default install.** Too easy to
  destroy a tenant with one bad row. Possible later but only behind
  its own opt-in group. See C5.

## 6. Out of scope: closing-the-gap-for-real

If a customer turns up needing the C1/C2 capabilities for real
money, the right move is probably **not** to build them into Studio
Light. Better options:

1. License Odoo Enterprise for that tenant and use Studio itself.
2. Write the customization as a regular versioned module — the B1
   extract-to-module wizard makes this a copy-paste-and-tighten job
   for a dev.
3. Use OCA's `report_qweb_*` modules for report tweaks rather than a
   full editor.

The function of Studio Light is to make the 80% case fast and
self-service, not to replicate every paid feature.
