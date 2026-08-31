# Blue Fox Credentials

The passwords, API keys and key files a project needs, encrypted at rest, with an
expiry calendar and a rotation that leaves a trace.

*Developed and maintained by [Blue Fox Inc.](https://bluefoxconsultant.com) This
module stores secrets at rest — see [SECURITY.md](SECURITY.md) for the trust model,
including what Fernet here does **not** protect against.*

> **Extracted from `project_knowledge_matrix` 18.0.13.0.0.** The models, their table
> and their external IDs were **reassigned**, never recreated. The encryption key
> lives in `ir.config_parameter`, not in module data, so stored secrets stay readable
> across the move. See [Upgrading from project_knowledge_matrix](#upgrading-from-project_knowledge_matrix).

## Overview

- **Encrypted at rest** — Fernet symmetric encryption for passwords and API keys; the
  plaintext columns do not exist
- **Key files as attachments** — SSH keys, certificates (`.pem`, `.key`, `.p12`,
  `.ppk`), never a table column
- **Nine credential types**, each showing only the fields it needs
- **Expiry calendar** — a daily pass moves credentials *out of* the active state, into
  "expiring" then "expired"
- **Restricted records** visible to managers only
- **Password rotation** with a reason and a last-rotated date
- **Smart button** on the project form, and a block on the knowledge dashboard
- **Three figures** added to the biweekly dashboard email, each with its drill-down

## Features

### The status is the accounting

A credential's `state` is the module's own accounting, written by the daily scheduled
action. Counters read the **status**, never the expiry date.

This distinction was a defect for months: the dashboard and the biweekly email looked
for credentials that were `active` **and** past their expiry date — a population the
cron empties by definition, because moving them out of `active` is exactly what it
does. Both reported "0 expired" permanently, on a database holding several. Fixed at
`project_knowledge_matrix` 18.0.11.4.0, and the drill-down domains were wrong the same
way.

`tests/test_dashboard.py` keeps them honest: it runs the cron, then checks that each
drill-down returns exactly the number its counter announces.

### What it adds to the knowledge dashboard

A "Credentials" card, restored as the third column of the quality/matrices row through
the `project_knowledge_matrix.DashboardQualityColumns` extension point rather than by
an xpath onto a neighbouring card.

It also overrides `_get_project_domain`: the base module no longer knows
`project.credential`, and without the override the card would count the **whole**
estate under a project filter — which reads as a count *of* that project.

## Installation

1. Copy `bf_credentials` next to `project_knowledge_matrix` in the addons path
2. Update the apps list
3. Install **Blue Fox Credentials**

Dependencies: `project_knowledge_matrix`, and the `cryptography` Python package.
Without `cryptography`, `_encrypt_value` logs a warning and stores **plaintext** — it
does not raise. The manifest declares it and a test asserts the declaration stays.

## Upgrading from project_knowledge_matrix

Databases that carried the vault inside `project_knowledge_matrix` must take this
module **in the same run** as the base module's 18.0.13.0.0:

```bash
odoo -d <db> -u project_knowledge_matrix -i bf_credentials --stop-after-init
```

The base module's `pre-migrate` pass reassigns every vault external ID — declared
records, and the ones Odoo generates by reflecting the code — *before* the base module
loads. Without it, Odoo would drop the three models at the end of the load, as records
the updated module no longer names.

Two things travel that are easy to miss:

- **`project.project.credential_ids`**, a `One2many` **typed** at
  `project.credential`. While it lived in the base module, the base module depended
  hard on the vault and the extraction was circular. It moves here, along with
  `credential_count` and the project smart button.
- **The three drill-down actions are named `report_action_cred_*`**, not
  `…_credential_*`. A pattern written as `%credential%` misses all three.

The pass refuses to run rather than lose data:

| Situation | What happens |
|-----------|--------------|
| This module present | External IDs reassigned; the table is untouched |
| Absent, vault empty, nothing extends the models | The empty tables are removed cleanly; the key stays in `ir.config_parameter` |
| Absent, credentials present | The upgrade stops with the record counts |

Nothing else needs a `depends` change. Four Blue Fox modules reference
`project.credential` — `bf_home`, `hosting_management`, `privacy_consent`,
`bf_universal_search` — but all through a registry guard or a plain string, never a
typed field or an external ID.

### Proving the secrets survived

Do not take the round trip on faith. `_decrypt_value` catches `InvalidToken` and
returns the ciphertext **unchanged**, so a wrong key produces no error — only a long
`gAAAAA…` string where a password should be.

`tests/test_extraction.py::test_every_stored_secret_still_decrypts` reads every secret
in the database and fails if any one comes back as its own ciphertext. No plaintext is
compared or logged.

Belt and braces, outside Odoo: hash each decrypted value before and after the move and
diff the two lists. On the Blue Fox production copy, 76 credentials produced identical
SHA-256 fingerprints on both sides, with zero decryption failures.

## Data Model

### project.credential

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Credential name |
| reference | Char | Internal reference |
| project_id | Many2one | Owning project — a credential without one is visible to nobody |
| type_id | Many2one | Credential type |
| partner_id | Many2one | Related contact |
| url / username / domain | Char | Connection details |
| password | Char | Non-stored; reads and writes through the encrypted column |
| password_encrypted | Char | Fernet token; not exposed in any view |
| api_key_encrypted | Char | Fernet token; not exposed in any view |
| key_file / key_filename | Binary / Char | Attachment-backed key file |
| environment | Selection | Environment tag |
| state | Selection | active / expiring / expired / revoked — written by the daily pass |
| expiration_date | Date | Drives the daily pass |
| last_verified / last_rotated | Date | Audit trail |
| restricted | Boolean | Managers only |
| notes | Text | Free notes |

### project.credential.type

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Type name |
| code | Char | Unique code |
| show_* | Boolean | Which fields the form shows for this type |

### project.project (extended)

| Field | Type | Description |
|-------|------|-------------|
| credential_ids | One2many | The project's credentials |
| credential_count | Integer | Computed, drives the smart button |

## Scheduled Actions

| Action | Frequency | What it does |
|--------|-----------|--------------|
| Vérifier les identifiants expirant | Daily | Moves credentials out of `active` into `expiring` then `expired` |

## Testing

```bash
odoo -d <db> -i bf_credentials --test-enable --stop-after-init
```

Four files: the vault's own net (`test_credentials.py`), the dashboard counters and
their drill-downs (`test_dashboard.py`), the module boundaries
(`test_module_boundaries.py`), and the extraction invariants (`test_extraction.py`).

The dashboard counters are measured as a **delta**, not an absolute. The original
version asserted "expiring == 1" and failed on any database that already held an
expiring credential — that is, on every production copy, which is exactly where a
migration gets tested.

## License

LGPL-3. See [LICENSE](LICENSE).
