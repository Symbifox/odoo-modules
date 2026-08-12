# bf_mass_notes

Adds a contextual **list-view Action** — *"Ajouter une note (en lot)"* — to every
chatter-enabled model. Select several records, open the **Action** menu, type a note
once, and it is posted to each selected record's chatter in a single step.

A `post_init_hook` creates one server-action binding for every concrete, non-transient
model that inherits `mail.thread`, so the Action appears wherever a chatter exists. The
matching `uninstall_hook` removes those bindings on uninstall. Re-running install/upgrade
is idempotent — existing bindings are not duplicated.

## Dependencies

- `mail`

No other dependency — the module is installable standalone.

## Security

- New transient model `bf.mass.note.wizard`, granted to internal users
  (`base.group_user`); it is not exposed to portal/public users.
- Posting uses `record.message_post(...)` with **no `sudo()`**, so Odoo's standard
  access control applies: the user must hold the access required by each model's
  `_mail_post_access` (default `write`) on every selected record. Records the user
  cannot post to are skipped and reported as failures — never bypassed.
- The note body is a sanitized `Html` field (`sanitize_style=True`); `message_post`
  sanitizes again on store.
- The generated server actions use a **static** `state="code"` snippet (no user input
  is interpolated into it), evaluated inside Odoo's `safe_eval` sandbox.

## UX

- **Action ▸ Ajouter une note (en lot)** in the list view of any chatter model
  (Tasks, CRM leads, Contacts, Helpdesk tickets, Invoices, Sales orders, …).
- The wizard shows the target model and the number of selected records.
- **Type** toggle:
  - *Note interne (journal)* — internal log note (`mail.mt_note`), no notification (default).
  - *Message (notifie les abonnés)* — a message (`mail.mt_comment`) that notifies followers;
    gated by an explicit confirmation checkbox and a warning banner, since this can generate
    emails across many records at once.
- A success/warning toast reports how many notes were posted, and any failures.

## Architecture

```
bf_mass_notes/
├── __manifest__.py
├── hooks.py                       # post_init_hook / uninstall_hook — create/remove the per-model list bindings
├── security/ir.model.access.csv   # bf.mass.note.wizard → base.group_user
└── wizard/
    ├── mass_note_wizard.py        # TransientModel bf.mass.note.wizard (default_get + action_post)
    └── mass_note_wizard_views.xml # wizard form view (bindings are created by the hook, not by XML)
```

## Note

The Action is bound to **all** concrete `mail.thread` models present at install time —
this can be a few dozen bindings on a typical database. This is intentional, so the
feature is available from every chatter-bearing list view.
