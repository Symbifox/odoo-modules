# Security audit — `bf_studio_light`

**Audit date:** 2026-05-10
**Scope:** all code in this module (v18.0.2.0.0 baseline)
**Auditor:** Internal review before any production deployment

This document tracks findings, severity, and resolution status. New
findings should be appended; resolved findings stay for traceability.

---

## Threat model

The module exposes admin-level capabilities — creating database fields,
modifying views, registering inheriting `ir.ui.view` records — to users
in the `Forge: Administrator` group. The threat model assumes:

- The group is granted only to trusted admins.
- All other users are NOT in the group.
- An attacker may have control of one in-group admin user.
- An attacker may craft XML-RPC payloads bypassing the wizard UI.
- Tenant data (partners, leads, invoices) must remain confidential
  even from in-group admins where Odoo's standard ACLs would prevent
  reads.

The audit focuses on **privilege escalation beyond what the admin
already has**, **information disclosure of fields the admin cannot
otherwise read**, and **persistence of malicious arch XML** that
affects all users.

---

## Findings

### S1 — XPath / XML / arch injection (HIGH) — RESOLVED v18.0.3.0.0

`custom_xpath`, `target_field`, and `arch_snippet` were interpolated
directly into a generated `<xpath expr="..." position="...">…</xpath>`
arch XML.

**Exploit:** an admin sets `arch_snippet` to a `<button name="X" type="action"/>`
pointing to a server action they don't have direct rights to invoke
(but anyone *viewing* the form does, as the button runs in the viewer's
session). Or sets `target_field` to `x' or '1'='1` to manipulate xpath
selection broadly.

**Fix:** added a strict whitelist of allowed XML element names in the
inner arch (`field`, `group`, `notebook`, `page`, `separator`, `label`,
`div`, `span`, `newline`); rejected `<button>`, `<header>`, `<form>`
sub-elements; validated xpath compiles via `etree.XPath()`; restricted
`target_field` to identifier regex `[a-zA-Z_][a-zA-Z0-9_]*`.

### S3 — Related path sudo + sensitive field exposure (HIGH) — RESOLVED v18.0.3.0.0

`_resolve_related_target` walked the path with `IMF.sudo()`, allowing
an admin to expose fields they wouldn't normally read (e.g.,
`password_crypt`, `api_key`, OAuth tokens) through a related field
visible on a less-restricted parent.

**Fix:** the path resolution still uses sudo (because reading
`ir.model.fields` is normal for admins) but added a SENSITIVE_FIELD_NAMES
denylist (passwords, tokens, secrets) AND model-level locking
(traversed model must not be in `LOCKED_MODELS`).

### S5 — LOCKED_MODELS coverage gaps (HIGH) — RESOLVED v18.0.3.0.0

Missing from the lock list: `mail.template`, `ir.config_parameter`,
`ir.attachment`, payment provider models, OAuth provider models, and
the `account.*` family. Adding fields to these could expose secrets
or allow phishing payloads to be persisted.

**Fix:** extended `LOCKED_MODELS_EXACT` with `mail.template`,
`ir.config_parameter`, `ir.attachment`, `auth.totp.*`,
`payment.provider`, `payment.token`, and added `account.` to the
prefix list. `mail.` prefix added (was only `mail.message`).

### S6 — `studio_light_force=True` was a context-only bypass (MEDIUM) — RESOLVED v18.0.3.0.0

The lock bypass relied on `self.env.context.get('studio_light_force')`,
which any caller (including XML-RPC clients) can set freely. This made
the bypass entirely user-controlled — not a guardrail.

**Fix:** replaced context check with a dedicated group
`group_studio_light_unlocked` (NOT implied by `group_studio_light_admin`)
that must be manually granted by a sysadmin. Context bypass removed.

### S12 — `position='replace'` allows core element removal (MEDIUM) — RESOLVED v18.0.3.0.0

Admins could replace required fields, security buttons, or entire form
sections via `position='replace'`, breaking the form for all users.

**Fix:** removed `replace` from `POSITION_CHOICES`. Only `after`,
`before`, `inside` allowed. Admins who need replacement can use a
proper Odoo module.

### S2 — `default_value` was misleading dead code (LOW) — RESOLVED v18.0.3.0.0

The field was stored on `studio.light.field` but never propagated to
the underlying `ir.model.fields`. The help text suggested Python-literal
parsing, which would have been an eval risk if wired.

**Fix:** removed the field. Default values are managed via the
`Customization > Default values` menu (which wraps `ir.default`).

### S4 — `sudo()` discipline (LOW) — DOCUMENTED

`_ensure_ir_model_field` and `_sync_selection_values` use sudo to
create `ir.model.fields` and `ir.model.fields.selection` rows.
This is intentional: the wizard requires the studio admin role
which permits this, and the sudo is necessary because the user
may not have direct ACL on `ir.model.fields` even with the studio
admin role.

**Mitigation in place:** access to `studio.light.field` is gated by
`group_studio_light_admin` (CSV ACL). All sudo'd creates flow through
that gate.

### S7 — Recovery cron has no failure backoff (LOW) — RESOLVED v18.0.3.0.0

A permanently broken record (model deleted, model locked after
creation, etc.) would loop daily logging errors.

**Fix:** added `failed_count` Integer + `last_failure_message` Char
to `studio.light.field` and `studio.light.view.injection`. After
3 consecutive failures, record is auto-deactivated.

### S10 — Arch validation didn't check XPath compileability (LOW) — RESOLVED v18.0.3.0.0

`_build_full_arch` used `etree.fromstring()` for well-formedness only.
A syntactically valid arch with a bogus xpath would fail at view
render time, breaking the form for other users.

**Fix:** added `etree.XPath()` compile check before persisting.

---

## Tier 2.5 audit — Smart button generator (v18.0.4.0.0)

The `studio.light.smart.button` model + `/studio_light/smart_button/count`
controller + `studio_light_smart_button_widget` OWL widget were added
in v18.0.4.0.0 to ship Tier 2.5 without the originally-rejected RCE
surface (no `compute=` Python code generated, no server actions
constructed from user input).

### T25-S1 — Trusted-arch escape (MEDIUM) — DOCUMENTED

The standard `_validate_arch_snippet` whitelist forbids `<widget>`. The
smart button needs to inject one. The escape is bounded by **two**
checks evaluated together in the `_check_arch_snippet` constraint:

1. `studio_smart_button_id` (M2o on `studio.light.view.injection`) must
   be set on the row.
2. `self.env.context.get("studio_light_trusted_arch")` must be true at
   the create/write that sets the snippet.

Both must hold. The widget tag is added to `TRUSTED_ARCH_EXTRA_TAGS`
only when both conditions are true.

**Bounded threat:** an admin in `group_studio_light_admin` (already
required to write `studio.light.view.injection`) can set both bits and
thus inject an arbitrary `<widget>`. This is identified as an accepted
risk: the same admin can already write Python in `ir.actions.server`,
so they are not the threat we defend against. The escape is designed
to prevent **typos and third-party module accidents** from accidentally
broadening the whitelist for non-smart-button rows. Test
`test_arch_trusted_only_for_smart_button_owned_rows` enforces that the
back-pointer alone (without context) doesn't bypass.

### T25-S2 — `target_action_id` deferred (HIGH if added) — NOT IMPLEMENTED

A future variant could let admins reuse an existing
`ir.actions.act_window`. Doing so is **out of scope** for v18.0.4.0.0:
existing actions can carry `domain` strings evaluated server-side and
chains of `code`-state server actions, which would make the smart
button a vehicle for executing pre-existing risky actions in
unintended contexts. The current `_build_action_dict` only assembles a
fresh dict from already-validated parts; no `target_action_id` field
exists on the model.

### T25-S3 — Domain DoS (LOW) — RESOLVED

A smart button on a heavy target with an unindexed `ilike` could pin a
worker for the duration of a tabe scan on every form open. The
controller wraps the `search_count` in `SET LOCAL statement_timeout =
'2s'`. `QueryCanceled` is mapped to a masked `timeout` envelope.

### T25-S4 — Domain literal-eval boundary (LOW) — RESOLVED

`ast.literal_eval` (NOT `safe_eval`) is used to parse the domain
field. `literal_eval` only returns Python literals (strings, numbers,
booleans, `None`, lists/tuples/dicts of literals). Function calls,
attribute access, and name lookups all raise `ValueError`, blocking
classic exploit attempts like `__import__('os').system('id')`.
Operators are further constrained to a hard-coded whitelist; field
paths must match `PATH_PART_RE` and not contain `SENSITIVE_FIELD_NAMES`.

### T25-S5 — Controller error masking (LOW) — RESOLVED

All exceptions inside the controller endpoint are caught and turned
into `{"count": 0, "action": false, "error": "<short_code>"}`. Full
tracebacks go to the server log. The widget renders `?` for any
non-empty `error` field. Short codes used: `invalid_args`,
`unavailable`, `forbidden`, `timeout`.

### T25-S6 — Cross-user cache poisoning (LOW) — MITIGATED

JSON-RPC requests to `/studio_light/smart_button/count` are POST and
typically not cached by intermediate proxies. The endpoint also
attempts to set `Cache-Control: private, no-store` via
`request.future_response.headers` (best-effort: the property is
sometimes absent depending on the dispatcher path).

### T25-S7 — Source-id boundary (LOW) — RESOLVED

The controller refuses non-int or non-positive `source_id` early. The
widget renders `–` (em dash) when the parent record has no `resId`
(unsaved new record), so the controller is never called with `0`.

### T25-S8 — Locked-model defence reused (HIGH) — RESOLVED

Both `source_model_id` and `target_model_id` go through the same
`is_model_locked()` check used by `studio.light.field`. The
`group_studio_light_unlocked` group is the only bypass; context flags
are not honored.

---

## Tier A1–A3 audit — Field-type expansion (v18.0.5.x)

### TA-S1 — Locked-model bypass via relational target (MEDIUM) — RESOLVED

`_check_model_allowed` originally validated only the **host model**
(`model_id`), not the **target model** on relational fields. A
non-unlocked admin could create `x_studio_link_to_users` on
`res.partner` with `relation_model_id = res.users` and end up with a
many2one pointing at a locked model — exposing user records through
the partner form. Pre-existing for many2one since v18.0.1; tightened
when extending the same code path to many2many and reference in
v18.0.5.0.

**Fix:** the constraint now also rejects `relation_model_id` and any
model in `reference_model_ids` that fails `is_model_locked()`, unless
the user is in `group_studio_light_unlocked`. Covered by
`tests/test_security.py::test_relational_target_locked_model_refused`
(many2many) and `test_reference_whitelist_locked_model_refused`
(reference).

## Tier A7 audit — Conditional modifiers (v18.0.6.0.0)

### TA-S2 — Modifier expression eval surface (HIGH if uncontrolled) — RESOLVED

A7 lets admins write Python-style expressions (`state == 'draft'`)
that Odoo's view engine then evaluates with `safe_eval` at render
time. Without a server-side guard, a user could insert
`__import__('os').system(...)` or comprehension-based introspection
chains that abuse `safe_eval`'s sandbox.

**Fix:** `validate_modifier_expression()` parses the expression with
`ast.parse(mode='eval')` and walks every node against
`_SAFE_MODIFIER_NODES`. Allowed: `BoolOp`, `UnaryOp`, `Compare`, a
restricted `BinOp` (Add/Sub/Mult/Div/Mod), `Constant`, `Name`,
`Attribute`, `Tuple`, `List`. Rejected by omission: `Call`,
`Subscript`, `Lambda`, `Comprehension`, `Starred`, `Import`. Covered
by `tests/test_security.py`:

- `test_modifier_expression_rejects_function_call`
- `test_modifier_expression_rejects_subscript`
- `test_modifier_expression_rejects_comprehension`
- `test_modifier_expression_accepts_idiomatic` (negative control)

---

## Findings deferred (post-Tier 2.5)

### Tier 2.7 — Translation inline

Touches JSONB across all records. Per-record edit UI requires careful
ACL plumbing to prevent admins from editing translations of records
they wouldn't read normally.

---

## Verification

After applying all fixes, the module was re-tested on the `staging`
DB. The full test plan is in `tests/test_security_v18_0_3.py` (run
via `odoo --test-tags bf_studio_light`).

Tests cover:

- ✅ Inject `<button>` via `arch_snippet` rejected
- ✅ XPath with logical operators (`or`, union `|`) rejected
- ✅ `target_field` with quotes rejected
- ✅ Related path through `password_crypt` rejected
- ✅ `studio_light_force=True` context alone has no effect (group required)
- ✅ `position='replace'` not selectable
- ✅ Records auto-deactivated after 3 failed integrity provisions
