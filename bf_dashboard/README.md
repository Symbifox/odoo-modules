# Symbifox dashboard (`bf_dashboard`)

A unified dashboard aggregating billing, hosting, knowledge matrices and
privacy into a single view.

## Features

- Summary cards aggregating several operational domains (billing, hosting,
  knowledge, privacy consents).
- A single entry point for daily follow-up.
- Extensible: other modules can add their own cards (see
  `bf_subscription_dashboard`).

> Note: on install, the `_set_home_action` `post_init_hook` redefines users'
> home action so this dashboard opens at the start of the session.

## Dependencies

`base`, `account`, `project`, `mail`, `hosting_management`,
`project_knowledge_matrix`, `privacy_consent`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
