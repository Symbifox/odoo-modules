# bf_credentials — Security model

This module stores **secrets at rest**: passwords, API keys and key files, kept per
project. This note is the trust model and the controls. It moved here from
`project_knowledge_matrix`'s SECURITY.md when the vault was extracted at that
module's 18.0.13.0.0.

## Access model

Two groups, under their own user-menu category ("Identifiants"):

| Group | Grants |
|---|---|
| `group_credential_user` | Read/write credentials of projects they follow |
| `group_credential_manager` | Everything, including restricted records, types and deletion |

`group_credential_user` **implies** `project.group_project_user`, so granting it also
grants base project access — but the reverse is **not** true: a plain project user has
no access to the vault until explicitly added to the group.

**Record rules** scope row visibility by project membership: a user only sees
credentials whose `project_id.message_partner_ids` includes them. Managers see
everything. `perm_unlink` is withheld from users.

There is deliberately **no "orphan" clause**: unlike documents, a credential with no
project is visible to nobody. A secret attached to nothing has no legitimate holder.

## Encryption & trust boundary

- Passwords and API keys are **encrypted at rest** with **Fernet** (symmetric
  AES-128-CBC + HMAC, from the `cryptography` package). The plaintext is never
  stored; only `*_encrypted` columns are persisted, and those columns are **not
  exposed in any view**. The decrypted value is surfaced only through a masked
  "copy" widget, to users who already pass the record rule.
- Key files are stored as **attachments**, never as a table column.
- **The encryption key lives in `ir.config_parameter`** under
  `project_credential.encryption_key`, auto-generated on first use. This is a
  deliberate trust boundary: the key sits in the **same database** as the ciphertext,
  so **anyone who can read that parameter — a system administrator
  (`base.group_system`), or anyone with direct database or backup access — can
  decrypt the vault.** Fernet here protects against casual row inspection and
  ORM-level leakage, **not** against a privileged DB or system operator.
- Hardening for deployers who need a stronger boundary:
  - Restrict `base.group_system` membership tightly — it is the de-facto vault root.
  - Source the key from outside the DB (env or secret manager) by overriding
    `_get_encryption_key`.
  - Encrypt database backups, since they contain both key and ciphertext.

### Why the key does not move with the module

The extraction from `project_knowledge_matrix` reassigns the table and its external
IDs. Had the key been a **module data record**, it would have been reassigned too —
and uninstalling the module would have taken it along, turning every stored secret
into an unreadable Fernet token in one step. Living in `ir.config_parameter`, it
survives both the move and an uninstall.

This is checked rather than assumed:
`tests/test_extraction.py::test_every_stored_secret_still_decrypts` reads every
secret in the database and fails if any one of them comes back **as its own
ciphertext** — which is what `_decrypt_value` returns, silently, when the key does
not match. No plaintext is ever compared or logged.

## A silent failure mode worth knowing

`_decrypt_value` catches `InvalidToken` and returns the input unchanged. That is
deliberate — it lets a database with pre-encryption plaintext keep working — but it
means a **wrong key produces no error**: the UI simply shows a long `gAAAAA…` string
where a password should be. If you see that, do not re-save the record: writing it
back would encrypt the ciphertext a second time.

Likewise, if `cryptography` is missing, `_encrypt_value` logs a warning and stores
**plaintext**. The dependency is declared in the manifest, and
`tests/test_module_boundaries.py` asserts that it stays declared.

## General posture

- **No external network calls** (no `requests`/`urllib`/`subprocess`), so no SSRF or
  command-injection surface.
- **No secrets in code or data files.** The shipped `data/*.xml` seeds nine credential
  *types* — reference data, no values.
- Credential names are deliberately **absent from the universal search index**: a
  search suggestion that names a vault entry leaks the inventory to anyone who can
  type in the command palette.

## Reporting

Found a vulnerability? Please contact [Blue Fox Inc.](https://bluefoxconsultant.com)
rather than opening a public issue.
