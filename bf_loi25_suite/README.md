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

GNU LGPL-3. See [`LICENSE`](LICENSE) for the full text.

## Changelog

| Version | Change |
|---|---|
| 18.0.1.1.0 | Added `bf_sign` (native electronic signature) to the bundle so the Loi 25 stack ships with electronic signature out of the box. |
