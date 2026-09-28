# project_knowledge_matrix — Security model

This module captures **chatter messages** into knowledge items and holds project
documentation. This note is the trust model and the controls.

> **The credential vault left in 18.0.13.0.0**, and corporate governance in
> 18.0.12.0.0. Their trust models moved with them, to
> [`bf_credentials/SECURITY.md`](../bf_credentials/SECURITY.md) and the
> `bf_corporate_governance` module. This module no longer stores secrets at rest and
> no longer depends on `cryptography`.

## Access model

Access is **opt-in per capability**, not granted to every internal user. The module
ships four group families (under distinct user-menu categories):

| Group | Grants |
|---|---|
| `group_knowledge_user` / `group_knowledge_manager` | Knowledge matrices & items |
| `group_document_user` / `group_document_manager` | Documents, versions, distribution |

Each `*_user` group **implies** `project.group_project_user` (so granting it also
grants base project access) — but the reverse is **not** true: a plain project user
has no access to documents or matrices until explicitly added to the group.

**Record rules** scope row visibility by project membership: a `*_user` only sees
records whose `project_id.message_partner_ids` includes them. `*_manager` groups see
everything. `perm_unlink` is withheld from `*_user` (managers only).

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
  (section and document types, document-section templates).
- The single raw SQL statement is the standard Odoo report-view pattern
  (`CREATE VIEW <self._table>`), with no user input interpolated.

## Reporting

Found a vulnerability? Please contact [Les services de consultation Blue Fox, Inc.](https://symbifox.com)
rather than opening a public issue.
