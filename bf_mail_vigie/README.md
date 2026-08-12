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
- `Target` field of type `Reference` combining a dropdown of the 12
  common models (task, CRM lead, helpdesk ticket, contact, sale order,
  invoice, etc.) and a Many2one picker filtered by the chosen model.
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

LGPL-3

---

<sub>Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.</sub>
