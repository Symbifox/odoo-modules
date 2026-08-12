# project_knowledge_matrix — Security model

This module stores **secrets at rest** (the credential vault: passwords, API keys,
key files) and captures **chatter messages** into knowledge items. This note is the
trust model and the controls.

## Access model

Access is **opt-in per capability**, not granted to every internal user. The module
ships four group families (under distinct user-menu categories):

| Group | Grants |
|---|---|
| `group_knowledge_user` / `group_knowledge_manager` | Knowledge matrices & items |
| `group_credential_user` / `group_credential_manager` | The credential vault |
| `group_document_user` / `group_document_manager` | Documents, versions, distribution |
| `group_corporate_manager` | Corporate governance (resolutions, directors, officers, compliance) |

Each `*_user` group **implies** `project.group_project_user` (so granting it also
grants base project access) — but the reverse is **not** true: a plain project user
has no access to credentials/documents/matrices until explicitly added to the group.

**Record rules** scope row visibility by project membership: a `*_user` only sees
records whose `project_id.message_partner_ids` includes them. `*_manager` groups see
everything. `perm_unlink` is withheld from `*_user` (managers only).

## Credential vault — encryption & trust boundary

- Passwords and API keys are **encrypted at rest** with **Fernet** (symmetric
  AES-128-CBC + HMAC, from the `cryptography` package). The plaintext is never stored;
  only `*_encrypted` columns are persisted, and those columns are **not exposed in any
  view**. The decrypted value is surfaced only through a masked "copy" widget to users
  who already pass the record rule.
- **The encryption key lives in `ir.config_parameter`** under
  `project_credential.encryption_key`, auto-generated on first use. This is a
  deliberate trust boundary: the key sits in the **same database** as the ciphertext,
  so **anyone who can read that parameter — i.e. a system administrator
  (`base.group_system`) or anyone with direct database/backup access — can decrypt the
  vault.** Fernet here protects against casual row inspection and ORM-level leakage,
  **not** against a privileged DB/system operator.
- Hardening recommendations for deployers who need a stronger boundary:
  - Restrict `base.group_system` membership tightly (it is the de-facto vault root).
  - Consider sourcing the key from outside the DB (env/secret manager) by overriding
    `_get_encryption_key`.
  - Encrypt database backups, since they contain both key and ciphertext.

## Capturing chatter into a knowledge item

The "Capture into the matrix" action copies a chatter message (body + attachments,
including any `.eml`) into a knowledge item's chatter. The source message is read
**under the calling user's own ACL** (`check_access('read')`, no `sudo`): a user can
only capture messages they are already allowed to read. There is no privilege
escalation via the capture path.

## General posture

- **No external network calls** (no `requests`/`urllib`/`subprocess`), so no SSRF or
  command-injection surface.
- **No secrets in code or data files.** Shipped `data/*.xml` seeds only reference data
  (section/credential/document types, generic compliance-event templates).
- The single raw SQL statement is the standard Odoo report-view pattern
  (`CREATE VIEW <self._table>`), with no user input interpolated.

## Reporting

Found a vulnerability? Please contact [Les services de consultation Blue Fox, Inc.](https://symbifox.com)
rather than opening a public issue.
