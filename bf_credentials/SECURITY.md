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
- **The encryption key lives outside the database** since 18.0.3.0.0: the
  `BF_CREDENTIALS_FERNET_KEY` environment variable, or `bf_credentials_fernet_key`
  in `odoo.conf`. **It is never generated automatically.** With no key configured,
  the module raises; it does not invent one.
- Until 18.0.3.0.0 the key was auto-generated into `ir.config_parameter`, which
  put it in the **same `pg_dump`** as the ciphertext it protects. Encryption at
  rest therefore did not protect against what people assumed it did: any copy of
  the database carried its own key.
- The old system parameter is still **read** as a last resort, so a dump taken
  before the switchover stays readable. It is never written and never generated,
  and it is refused for **writing** new secrets: encrypting fresh values with the
  key that sleeps in the database would reopen the very hole this closes.
- Remaining exposure, stated plainly: anyone who can read both the database and
  the server's configuration can still decrypt the vault. What changed is that a
  **database copy alone** is no longer enough, which is the case that actually
  occurs (backups, refreshed benches, a dump handed to someone).
- A useful side effect: a bench configured with its own key cannot read a
  production blob restored onto it.

### Why the key is not module data either

The extraction from `project_knowledge_matrix` reassigns the table and its external
IDs. Had the key been a **module data record**, it would have been reassigned too,
and uninstalling the module would have taken it along, turning every stored secret
into an unreadable Fernet token in one step. Living outside the database entirely,
it survives the move, an uninstall, and a restore.

This is checked rather than assumed:
`tests/test_extraction.py::test_every_stored_secret_still_decrypts` reads every
secret in the database and fails if any one of them cannot be decrypted. No
plaintext is ever compared or logged.

### Upgrading to 18.0.3.0.0

The upgrade **re-encrypts** every stored secret with the new key, because moving
the key is not enough on its own: every dump already taken still holds the old key
and would still open today's ciphertext. Re-encrypting is what retires those dumps.

Post it before you upgrade. With no key outside the database, the migration raises
and the upgrade stops with the database untouched, which is deliberate: an upgrade
that "succeeds" while leaving the secrets under the database's own key would be a
success in appearance only.

The migration is re-runnable, and it keeps the legacy parameter (only that key
opens backups taken before the switchover). Removing it is a separate, deliberate
step once the upgrade has been proven.

## Failures are loud, since 18.0.3.0.0

Both silent failure modes are gone. They were the second half of the same fix.

`_decrypt_value` used to catch `InvalidToken` and return its input unchanged, so a
**wrong key produced no error**: the screen showed a long `gAAAAA…` string where a
password should be, and re-saving the record encrypted the ciphertext a second
time. It now raises. On screen, the computed field shows a visible marker instead
of the value, and the inverse refuses to write that marker back, so a form save can
no longer double-encrypt anything.

`_encrypt_value` used to log a warning and store **plaintext** when `cryptography`
was missing or encryption failed. It now raises, and nothing is written. The
dependency is declared in the manifest, and `tests/test_module_boundaries.py`
asserts that it stays declared.

`verifier_chiffrement()` counts what is encrypted, what was left in the clear and
what no longer opens, over the whole vault or a given domain. It returns counts and
record references only, never a value. Credential managers only.

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
