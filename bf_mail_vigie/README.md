# bf_mail_vigie

Adds a "Re-route" button to the list and form views of `bf.email`
(module `bf_email_management`). Inspects the `In-Reply-To` and
`References` headers and the `parent_id` of the source `mail.message`
to suggest a more relevant target chatter. On confirmation, updates the
`model` / `res_id` columns of the `mail.message` (and the matching
`bf.email` projection), without resending or notifying.

## Dependencies

- `mail`
- `bf_email_management`
- `bf_onboarding_base`
- `bf_chatter_target`

## Security

- No new sensitive data is stored.
- The re-route operation = column mutation, no `message_post`,
  no `send_mail`, no `mail.mail` created.
- Requires `write` on the target record and on the source `bf.email`,
  `read` on the `mail.message`.
- The wizard is available to internal users (`base.group_user`),
  but the ACL on each target record is checked before mutation.

## UX

- "Re-route" button in the header of the `bf.email` form view.
- "Re-route this email" action menu available in the list view
  (multi-selection: one wizard per record).
- `Target` field of type `Reference` rendered by the shared
  `bf_chatter_target` widget (since 2.3.0): a single search box over every
  chatter-bearing model, grouped by model with an icon and a context line.
  The previous dropdown of 12 hardcoded models is gone — an agenda, a
  meeting record or a secure transfer could not be reached through it. The
  same box resolves a pasted Odoo URL, a bare id, a shorthand
  (`task:22299`) or a technical reference (`bf.email:17`).
- If an automatic suggestion is available (via headers or parent_id),
  an "Apply suggestion" button pre-fills the `Target` field.

## Architecture

```
bf_mail_vigie/
├── models/bf_email.py          # inherit + action_open_reroute_wizard
├── wizard/reroute_wizard.py    # TransientModel + action_reroute
├── wizard/reroute_wizard_views.xml
├── views/bf_email_views.xml    # inherit form header + button
└── security/ir.model.access.csv
```

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
