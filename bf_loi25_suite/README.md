# Symbifox — Loi 25 Suite

Bundle meta-module that pulls in the Symbifox Quebec **Loi 25** (Act respecting the protection of personal information) compliance stack on a single install.

## Included modules

| Module | Role |
|---|---|
| [`audit_ti`](../audit_ti) | IT security audit management |
| [`privacy_consent`](../privacy_consent) | Consents, document destruction, anonymization |
| [`project_knowledge_matrix`](../project_knowledge_matrix) | Knowledge base for compliance policies and documentation |
| [`bf_sign`](../bf_sign) | Native electronic signature (SES) for consent forms and compliance documents |

Installing `bf_loi25_suite` installs all four. Uninstalling it does **not** uninstall them — Odoo only cascades `depends` in one direction.

> **External prerequisites.** Through `bf_sign` → `bluefox_branding`, this bundle transitively requires the community modules `om_account_followup`, `contract` (OCA), and `l10n_ca`. Make sure they are in your `addons_path` before installing.

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

| Version | Change |
|---|---|
| 18.0.1.1.0 | Added `bf_sign` (native electronic signature) to the bundle so the Loi 25 stack ships with electronic signature out of the box. |
