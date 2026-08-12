# BF Document Nextcloud Sync

Connect Odoo 18 document management (`project_knowledge_matrix`) to Nextcloud file storage via WebDAV. Nextcloud stores the files, Odoo manages the metadata, versioning, and workflow.

**Published by [Les services de consultation Blue Fox, Inc.](https://symbifox.com)** | LGPL-3

## Features

- **WebDAV integration**: PROPFIND, GET, PUT, MKCOL operations directly from Odoo to Nextcloud
- **File metadata sync**: Last modified date, size, ETag, content type, and Nextcloud file ID retrieved via PROPFIND
- **Internal link**: One-click button copies the Nextcloud internal file link (`/f/{file_id}`) for sharing within your team
- **File upload**: Upload wizard sends files to Nextcloud via WebDAV PUT and optionally creates a `project.document.version` record
- **Public share links**: Generate password-protected, time-limited public links via the Nextcloud OCS API; password delivered via Odoo notification only (never posted to chatter)
- **Modification detection**: Daily cron compares ETags to detect files modified on Nextcloud, creates a draft version record and an activity for the document owner
- **Version tracking**: File changes detected by the cron automatically create `project.document.version` records with changelog (ETag diff, size delta, modification date)
- **Project folder mapping**: Associate a Nextcloud folder with each project; new documents inherit the path prefix and NC configuration
- **Folder browsing**: Stat button opens the document's parent folder directly in Nextcloud
- **Encrypted credentials**: App passwords stored with Fernet symmetric encryption (key sourced from environment variable, odoo.conf, or database with warning)
- **Security hardened**: HTTPS enforcement, SSRF protection, path traversal prevention, XSS-safe HTML output, share link expiration by default

## Architecture

```
              Nextcloud                              Odoo 18
         (WebDAV / OCS)                     (project_knowledge_matrix)
      +--------------------+              +-------------------------+
      |                    |              |                         |
      | /remote.php/dav/   |<-- PROPFIND -| Refresh from NC         |
      | files/user/        |              | action_refresh_from_nc  |
      |                    |              |                         |
      |                    |<-- GET ------| Download file           |
      |                    |              | _webdav_get()           |
      |                    |              |                         |
      |                    |<-- PUT ------| Upload wizard           |
      |                    |              | document.nc.upload.wiz  |
      |                    |              |                         |
      |                    |<-- MKCOL ----| Create directories      |
      |                    |              | _webdav_mkcol()         |
      |                    |              |                         |
      | /ocs/v2.php/apps/  |<-- POST -----| Create share link       |
      | files_sharing/     |              | _ocs_create_share()     |
      +--------------------+              +-------------------------+
                                                    |
                                          nextcloud.document.config
                                                    |
                                          project.document (inherited)
                                          nc_file_path, nc_etag,
                                          nc_file_id, nc_share_url
                                                    |
                                          project.document.version
                                          (auto-created on NC change)
```

### Design Principles

| Principle | Implementation |
|-----------|---------------|
| Nextcloud = storage | Files live on Nextcloud; Odoo never stores binary content (except optional `ir.attachment` on versions) |
| Odoo = metadata | Classification, versioning, workflow, distribution, and review lifecycle remain in `project.document` |
| No duplication | Documents link to NC by path, not by copying files into Odoo |
| No auto-import | The cron detects changes but does NOT create documents automatically (too risky without classification) |
| Security-first | Every NC interaction validates URLs, paths, and output; credentials encrypted at rest |

## File Structure

```
bf_document_nextcloud_sync/
+-- __init__.py
+-- __manifest__.py
+-- LICENSE                                # LGPL-3
+-- README.md
+-- models/
|   +-- __init__.py
|   +-- nextcloud_document_config.py       # Config model, WebDAV/OCS operations, encryption
|   +-- project_document.py                # project.document extension + NC fields + version tracking + cron
|   +-- project_project.py                 # project.project extension (NC folder mapping)
|   +-- res_config_settings.py             # Settings integration
+-- wizard/
|   +-- __init__.py
|   +-- document_nc_upload_wizard.py       # Upload file to NC + create version
|   +-- document_nc_upload_wizard_views.xml # Wizard form view
+-- views/
|   +-- nextcloud_document_config_views.xml # Config form/list/search + action
|   +-- project_document_views.xml         # NC buttons + fields on document form
|   +-- project_views.xml                  # NC folder on project settings tab
|   +-- res_config_settings_views.xml      # Knowledge Matrix settings section
|   +-- menu.xml                           # Configuration menu entry
+-- data/
|   +-- nextcloud_sync_cron.xml            # Daily modification check cron
+-- security/
    +-- ir.model.access.csv                # ACL: read for doc users, full for admins
```

## Installation

```bash
# Copy module to addons directory
cp -r bf_document_nextcloud_sync /path/to/odoo/addons/

# Install (or upgrade)
docker exec my-odoo odoo -d my-database -i bf_document_nextcloud_sync \
    --stop-after-init --http-port=9665

# Restart
docker restart my-odoo
```

**Dependencies**: `project_knowledge_matrix`, `bf_onboarding_base`

**Python dependencies**: `cryptography`, `defusedxml`, `requests` (typically pre-installed in Odoo Docker images)

## Configuration

### 1. Create a Nextcloud Configuration

Go to **Knowledge Matrix > Configuration > Nextcloud Documents**:

| Field | Description | Example |
|-------|-------------|---------|
| Name | Display name | `Nextcloud Symbifox` |
| Nextcloud URL | Base URL (HTTPS required) | `https://nextcloud.example.com` |
| WebDAV Path | WebDAV endpoint path | `/remote.php/dav/files/` |
| Nextcloud User | Username for auth | `svc-odoo` |
| App Password | Nextcloud app password (encrypted) | *(Settings > Security > App passwords)* |
| Share Expiry | Default share link duration | `30` days |
| Share Password | Auto-generate passwords on shares | Enabled |

Then click **Test Connection** to verify WebDAV access (PROPFIND Depth:0).

### 2. Configure Encryption Key (Recommended)

By default, the Fernet encryption key is stored in `ir.config_parameter` (same DB as the encrypted data). For better security, set the key externally:

**Option A: Environment variable** (recommended for Docker):
```bash
NC_DOC_SYNC_FERNET_KEY=your-fernet-key-here
```

**Option B: odoo.conf**:
```ini
nc_doc_sync_fernet_key = your-fernet-key-here
```

Generate a key with:
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

If neither is set, a key is auto-generated in `ir.config_parameter` with a warning in the logs.

### 3. Map Projects to Nextcloud Folders

On the project form, under **Settings > Nextcloud Documents** (admin-only):

| Field | Description | Example |
|-------|-------------|---------|
| Config Nextcloud Documents | Default NC config for this project | `Nextcloud Symbifox` |
| Nextcloud Folder | Document folder on NC | `/Symbifox/Documents/` |

When creating a new document in this project, the NC config and folder prefix are auto-filled.

### 4. Settings (Optional)

Under **Settings > Knowledge Matrix > Nextcloud Documents**:

| Setting | Description |
|---------|-------------|
| Default Configuration | Pre-selected NC config for new projects |
| Configure Nextcloud | Link to the config list |

## Usage

### Linking a Document to Nextcloud

1. Open or create a `project.document` record
2. Set the **Config Nextcloud** and **Nextcloud Path** (e.g., `/Symbifox/Documents/POL-SECURITE-FR.pdf`)
3. Click **Refresh NC** to fetch metadata (size, date, ETag, file ID)
4. The document now shows a "Nextcloud" tab with all file metadata

### Uploading a New Version

1. On a document linked to Nextcloud, click **Upload Version**
2. Select a file, set the destination path, version number, and change type
3. Click **Upload**: the file is sent via WebDAV PUT, metadata is refreshed, and a `project.document.version` record is created with the file attached

### Creating a Share Link

1. Click **Share Link** on a NC-synced document
2. A public link is generated via the OCS API with:
   - Expiration: configurable (default 30 days)
   - Password: random 16-character alphanumeric (if enabled)
   - Permissions: read-only
3. The share URL and expiry are posted to chatter (no password)
4. The password is shown in a sticky Odoo notification visible only to the current user
5. The password is also stored on the document (admin-only field) for later retrieval

### Internal Link

Click **Lien interne NC** to get the Nextcloud internal URL (`/f/{file_id}`) in a sticky notification. This link is for internal team use and requires Nextcloud authentication.

### Folder Navigation

Click the **Nextcloud** stat button to open the file's parent folder directly in the Nextcloud web interface.

### Modification Detection and Version Tracking (Cron)

A daily cron job (`_cron_check_nc_modifications`) monitors all NC-linked documents:

1. Groups documents by NC configuration
2. For each document, sends a PROPFIND Depth:0 to get the current ETag
3. Compares against the stored `nc_etag`
4. If changed:
   - Updates NC metadata (etag, size, last_modified)
   - Creates a `project.document.version` record in `draft` state with a changelog detailing the ETag diff, size delta, and modification timestamp
   - Schedules a "To-Do" activity for the document owner
5. If first check (no stored ETag): stores the ETag silently (no version, no activity)
6. Updates the config's sync status with counts

The auto-created version record stays in `draft` state so the document owner can review the change, update the version metadata (change type, summary), and release it through the normal versioning workflow.

## Models

### nextcloud.document.config

Configuration for a Nextcloud WebDAV connection. One config per Nextcloud instance.

**Key Methods:**

| Method | HTTP | Description |
|--------|------|-------------|
| `_webdav_propfind(path, depth)` | PROPFIND | List files/get metadata; returns parsed list of dicts |
| `_webdav_get(path)` | GET | Download file content (bytes) |
| `_webdav_put(path, content, type)` | PUT | Upload file |
| `_webdav_mkcol(path)` | MKCOL | Create directory (idempotent, 405 = already exists) |
| `_ocs_create_share(path, ...)` | POST | Create public share link via OCS API |
| `action_test_connection()` | PROPFIND | Test WebDAV reachability |

**Encryption:**

| Method | Description |
|--------|-------------|
| `_get_encryption_key()` | Reads key from env var > odoo.conf > ir.config_parameter |
| `_encrypt_value(value)` | Fernet encrypt; raises `UserError` on failure (no plaintext fallback) |
| `_decrypt_value(encrypted)` | Fernet decrypt; returns as-is for legacy unencrypted data |

**PROPFIND Response Parsing:**

Each file entry contains:

| Key | Source | Description |
|-----|--------|-------------|
| `href` | `d:href` | WebDAV path |
| `name` | Derived | Filename (basename of href) |
| `is_dir` | `d:resourcetype` | True if collection |
| `last_modified` | `d:getlastmodified` | RFC 2822 date string |
| `size` | `oc:size` or `d:getcontentlength` | File size in bytes |
| `content_type` | `d:getcontenttype` | MIME type |
| `etag` | `d:getetag` | ETag for change detection |
| `file_id` | `oc:fileid` | Nextcloud internal file ID |

### project.document (Inherited)

**Added Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `nc_config_id` | Many2one | Nextcloud configuration |
| `nc_file_path` | Char | File path on Nextcloud (tracked) |
| `nc_file_id` | Integer | Nextcloud internal file ID |
| `nc_share_url` | Char | Public share link URL |
| `nc_share_password` | Char | Share password (admin-only) |
| `nc_share_expiry` | Date | Share link expiration date |
| `nc_last_modified` | Datetime | Last modified on Nextcloud |
| `nc_file_size` | Integer | File size in bytes |
| `nc_file_size_display` | Char | Human-readable size (computed) |
| `nc_etag` | Char | ETag for change detection |
| `nc_content_type` | Char | MIME type |
| `nc_synced` | Boolean | True if nc_file_path + nc_config_id set (computed, stored) |

**Action Buttons:**

| Button | Method | Visibility | Description |
|--------|--------|------------|-------------|
| Lien interne NC | `action_copy_nc_internal_link()` | NC-synced or has external_url | Shows NC internal link in notification |
| Rafraichir NC | `action_refresh_from_nextcloud()` | NC-synced | PROPFIND to update metadata |
| Lien de partage | `action_create_share_link()` | NC-synced | Create OCS public share; password in notification only |
| Televerser version | `action_upload_new_version()` | Has NC config | Open upload wizard |
| Nextcloud (stat) | `action_browse_nc_folder()` | NC-synced | Opens parent folder in Nextcloud |

**Constraints:**

| Constraint | Field | Validation |
|------------|-------|------------|
| Path traversal | `nc_file_path` | Rejects `..`, null bytes, control chars |
| Path prefix | `nc_file_path` | Must be under project's `nc_documents_folder` |
| Filename chars | `nc_file_path` | Rejects `<`, `>` in filename |
| HTTPS | `nc_share_url` | Must start with `https://` |

### project.project (Inherited)

**Added Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `nc_documents_config_id` | Many2one | Default NC config for project documents |
| `nc_documents_folder` | Char | NC folder path (e.g., `/Symbifox/Documents/`) |

### document.nc.upload.wizard (TransientModel)

Upload wizard for sending files to Nextcloud and creating version records.

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `document_id` | Many2one | Target document (readonly) |
| `nc_config_id` | Many2one | NC configuration |
| `nc_file_path` | Char | Destination path on NC |
| `file_data` | Binary | File to upload |
| `file_name` | Char | Original filename |
| `version_number` | Char | Version string (e.g., "1.0") |
| `change_type` | Selection | major / minor / patch / editorial |
| `change_summary` | Text | What changed |
| `create_version_record` | Boolean | Create `project.document.version` (default: True) |

**Upload Flow (`action_upload`):**

1. Decode base64 file data
2. MKCOL parent directory (idempotent)
3. PUT file to Nextcloud
4. PROPFIND to get ETag, file ID, and metadata
5. Update `project.document` with NC fields
6. Create `project.document.version` + `ir.attachment` (optional)
7. Post upload confirmation to chatter

## Cron Jobs

| Cron | Interval | Method | Description |
|------|----------|--------|-------------|
| Check NC Modifications | Daily | `_cron_check_nc_modifications()` | ETag comparison on all NC-linked documents; creates version record + activity on change |

The cron groups documents by NC config to minimize authentication overhead. Per-document errors are caught and logged without blocking other documents. Config sync status is updated with counts after each run.

## Security

### Access Control

| Model | Group | Read | Write | Create | Delete |
|-------|-------|------|-------|--------|--------|
| `nextcloud.document.config` | Document User | Yes | No | No | No |
| `nextcloud.document.config` | System (Admin) | Yes | Yes | Yes | Yes |
| `document.nc.upload.wizard` | Document User | Yes | Yes | Yes | Yes |
| `document.nc.upload.wizard` | Document Manager | Yes | Yes | Yes | Yes |

Password and TLS fields on the config model have `groups="base.group_system"` (admin-only in UI).

### Security Mitigations

This module was designed with a security audit of the existing `calendar_nextcloud_sync` and `contacts_nextcloud_sync` modules. The following vulnerabilities were identified and mitigated:

| # | Vulnerability | Severity | Mitigation |
|---|---|---|---|
| 1 | Plaintext password fallback | CRITICAL | `cryptography` is a mandatory dependency; `_encrypt_value()` raises `UserError` on failure instead of storing plaintext |
| 2 | SSRF on Nextcloud URL | CRITICAL | `@api.constrains` enforces HTTPS; rejects private/link-local IPs (RFC 1918, 169.254.0.0/16) |
| 3 | Fernet key in database | HIGH | Key read from `NC_DOC_SYNC_FERNET_KEY` env var or `odoo.conf` first; DB fallback logs a warning |
| 4 | Permissive config ACL | HIGH | Config model: read-only for Document Users, full CRUD for System only; password fields restricted to `base.group_system` |
| 5 | No explicit TLS verification | HIGH | `verify=True` explicit on every `requests` call; optional `ca_bundle_path` for custom CAs; `allow_insecure` flag (default False) for dev only |
| 6 | Path traversal | HIGH | `_sanitize_nc_path()` rejects `..`, null bytes, control chars; `_validate_path_under_prefix()` enforces project folder boundary |
| 7 | Unprotected share links | HIGH | Default 30-day expiry; random 16-char password; read-only permissions; password never in chatter |
| 11 | Filename injection | MODERATE | `@api.constrains` rejects `<`, `>` in filenames |
| 12 | Share URL injection | MODERATE | `@api.constrains` enforces HTTPS on share URLs |
| 13 | Sensitive data in logs | MODERATE | Only operation type, path (truncated), and HTTP status are logged; never passwords or file contents |
| XSS | HTML injection in chatter | HIGH | All external values (filenames, URLs, dates) escaped with `markupsafe.escape()` before HTML interpolation; `body_is_html=True` on all `message_post` calls |

### Corrections Recommended for Existing Modules

The same audit revealed weaknesses in `calendar_nextcloud_sync` and `contacts_nextcloud_sync` that should be addressed separately:

- Add `verify=True` explicit on all `requests` calls
- Add `@api.constrains` HTTPS on `calendar_nextcloud_sync` (contacts already has it)
- Make `cryptography` mandatory in `calendar_nextcloud_sync` (contacts already raises `ValueError`)
- Restrict config model read access from `base.group_user` to `base.group_system`
- Move Fernet key out of `ir.config_parameter`

## Troubleshooting

| Issue | Check |
|-------|-------|
| Test connection fails | URL correct? HTTPS? Path ends with `/`? App password (not main password)? |
| "Refresh NC" shows file not found | Verify `nc_file_path` matches the actual NC path (case-sensitive) |
| Upload fails with 401 | App password valid? User has write permission on the target folder? |
| Upload fails with 409 | Parent directory doesn't exist; the wizard tries MKCOL but it may fail on deep paths |
| Share link creation fails | `files_sharing` app enabled on NC? User has share permissions? |
| Modification cron does nothing | Documents need both `nc_config_id` and `nc_file_path` set; first run stores ETags silently |
| Encryption errors | `cryptography` library installed? Check env var / odoo.conf / `ir.config_parameter` for key |
| Path validation rejects valid path | Check for `..` segments, null bytes, or `<>` in filename |
| NC fields not visible on document form | Module installed? Check for XML view inheritance errors in Odoo logs |
| Settings page empty | Admin access required; look under Knowledge Matrix > Nextcloud Documents |
| Chatter shows double-escaped HTML | Ensure `body_is_html=True` on all `message_post` calls |

## Limitations

1. **No inline preview**: Files are not previewed in Odoo; use the internal link or Nextcloud stat button
2. **No bidirectional sync**: Changes on NC are detected but not auto-imported into Odoo documents
3. **No recursive folder scan**: PROPFIND uses Depth:1 only (flat listing of one folder)
4. **Single file per document**: Each `project.document` links to one NC file path
5. **File ID resolution**: `oc:fileid` requires PROPFIND (no direct path-to-ID lookup)
6. **Multi-company**: One NC config per company; cross-company document linking not supported
7. **Phase 2 pending**: JS file browser widget, recursive cron scan, and share expiry monitoring are planned but not yet implemented

## Roadmap (Phase 2)

- **Owl file browser widget**: Interactive file tree in the document form, calling a controller that proxies PROPFIND
- **Recursive folder scan cron**: Detect new files in project folders and create activities (not auto-import)
- **Share expiry monitoring**: Cron to flag/revoke expired share links
- **Key rotation**: `MultiFernet` support for Fernet key rotation without re-encryption downtime
- **CSP headers**: Content-Security-Policy on controller responses for the JS widget

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.3.0 | 2026-06-21 | Synchronisation documentation et métadonnées (licence/LICENSE). Voir l'historique git pour le détail des correctifs intermédiaires. |
| 18.0.1.1.0 | 2026-03-08 | "Lien interne NC" replaces "Ouvrir dans Nextcloud"; share password removed from chatter (notification only); folder browse opens NC directly; NC file changes create draft version records with changelog; fix double HTML escaping in chatter (`body_is_html=True`); relax filename constraint (allow `'` and `&`); LGPL-3 license |
| 18.0.1.0.0 | 2026-03-07 | Initial release: config model, WebDAV PROPFIND/GET/PUT/MKCOL, OCS share API, document form integration, upload wizard, daily modification cron, project folder mapping, security hardening |

## License

LGPL-3 - Copyright (c) 2026 [Les services de consultation Blue Fox, Inc.](https://symbifox.com)

See [LICENSE](LICENSE) for full text.

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of this software.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
