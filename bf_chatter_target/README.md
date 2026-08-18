# Blue Fox Chatter Target

> **Not a standalone module.** `bf_chatter_target` is a library other modules
> depend on. Installing it directly shows nothing: no menu, no settings page.
> Install a consumer (`bf_bloc_notes`, `bf_mail_vigie`, `bf_mail_import`) and it
> comes along as a dependency.

One way to answer the only question every importer asks: **which record
receives this?**

## Why

Every importer ends up asking the same question, and each had grown its own way
of asking it. The table below is the full picture across the suite; the modules
published alongside this one are `bf_bloc_notes`, `bf_mail_vigie` and
`bf_mail_import`.

| Surface | Picker before |
|---|---|
| `bf.email.reroute` — import an IMAP email | model dropdown + record, plus a separate "quick paste" field |
| `bf.note.reroute` — re-route a note | model dropdown + record, plus its own, divergent "quick paste" |
| `bf.mail.reroute.wizard` — re-route a routed email | model dropdown limited to **12 hardcoded models** |
| `sms.archive.post.to.task.wizard` — post SMS / calls | **Project + Task**, tasks only |
| `bf.note.link` / `bf.note.res_ref` / note→activity | model dropdown + record |
| IMAP browser quick route | a menu of three model hints |

Two copies of the "paste a link" resolver had forked, and the twelve hardcoded
models meant an agenda or a secure transfer could never be reached.

## What it provides

* **`bf.chatter.target`** (an `AbstractModel`: no table, no ACL — a
  `_auto = False` model would log a `Model … has no table` error on every
  registry load for nothing)
  * `_thread_model_selection()` — every non-transient `mail.thread` model,
    priority models first. Restrictable through the system parameter
    `bf_chatter_target.models` (the legacy `bf_bloc_notes.reference_models` is
    still honoured so existing databases keep their restriction).
  * `_resolve(text)` — resolves an Odoo URL (18.0 `/odoo/<action>/<id>`, legacy
    `/web#model=…&id=…`, `/odoo/project/<pid>/<tid>`), a technical reference
    (`bf.email:17`), a shortcut (`task:22299`, `facture#42`), a bare id or an
    invoice name (`INV/2026/00017`). Never raises, always access-checked.
  * `search_targets(query, limit=5)` — the RPC behind the picker. Same result
    shape as `bf.universal.search.search_all`, whose configurations it reuses
    when that module is installed (icons, context line, struck-out closed
    records) and falls back to `name_search` otherwise. The first group, when
    present, is the exact reference parsed out of the query — that is what
    replaced the separate "quick paste" field.
* **`bf.chatter.target.mixin`** — the `target_reference` field, its selection,
  and `_get_chatter_target(operation)` which validates existence, chatter
  support and access rights in one call.
* **The `bf_chatter_target` widget** — one input, grouped results, no model to
  pick first. Use it on any `Reference` field:

  ```xml
  <field name="target_reference" widget="bf_chatter_target" required="1"/>
  ```

## Dependencies

`web`, `mail`. `bf_universal_search` is used when present but is **not** a
dependency: a tenant can install a consumer without pulling the whole search
module in.

## Tests

```
odoo -d <db> -u bf_chatter_target --test-enable --test-tags /bf_chatter_target
```
