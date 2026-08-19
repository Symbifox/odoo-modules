# Nextcloud File Browser

Embedded and standalone WebDAV file browser for Nextcloud, integrated into Odoo
project and task forms. Browse, preview, upload, organise and share files stored
on a Nextcloud instance — without leaving Odoo.

- **Author:** Les services de consultation Blue Fox, Inc.
- **License:** LGPL-3
- **Odoo:** 18.0
- **Depends:** `bf_document_nextcloud_sync`, `project`, `project_knowledge_matrix`

## Features

- **Embedded tab** *Fichiers Nextcloud* on `project.project` and `project.task`
  forms — scoped to each record's Nextcloud folder.
- **Standalone app** — a top-level *Nextcloud* application that browses from a
  configured root prefix (no record context).
- **Dolphin-style two-pane layout** — a lazy-loaded folder tree on the left and
  the directory listing on the right.
- **File operations** — browse, breadcrumb navigation, upload (button or
  drag-and-drop, multi-file), create folder, rename, move (drag a row onto a
  folder), delete.
- **Preview** — inline modal preview for PDF, images and text, served
  same-origin by Odoo.
- **Open in Nextcloud** — clicking an office document (configurable extension
  list) opens it directly in Nextcloud (e.g. Collabora) in a new tab.
- **Sortable columns** — Name / Type / Modified / Size, folders pinned first.
- **Public share links** — configurable presets (e.g. internal read/write,
  external read-only with expiry and optional password).
- **Knowledge integration** — link a file to a Knowledge Matrix item
  (`project.knowledge.item`) or, where available, an Odoo Knowledge article.
- **Systray launcher** — a toggleable button that opens the full Nextcloud web
  app in a large popup window.

## Security model

The OWL widget and the streaming controller talk to Nextcloud **only** through
the `bf.nc.browser` facade. Every entry point:

- requires membership in the *Navigateur Nextcloud* group
  (`group_nc_browser_user`);
- re-derives the configuration and folder root from the **record**, never from a
  client-supplied path or config id (prevents IDOR);
- enforces `record.check_access("read")` before listing a record's files;
- sanitises every path (rejects `..`, NUL/control chars, URL-encoded bypasses)
  and validates it stays under the record root, which itself must stay under the
  config `browser_root_prefix`;
- the standalone app refuses to operate unless a meaningful root prefix is set.

Binary preview responses are served with `X-Content-Type-Options: nosniff` and a
`script-src 'none'` CSP so a malicious HTML/SVG file cannot execute in the Odoo
origin. Share-link permissions are derived server-side; HTML inserted into
Knowledge articles is escaped.

Credentials are never handled by this module — it reuses the encrypted
service-account configuration provided by `bf_document_nextcloud_sync`.

## Configuration

1. Open **Nextcloud → Configuration** (admin only).
2. On the *Symbifox Nextcloud* configuration record set the Nextcloud base URL,
   WebDAV path, service-account user and app password.
3. Set **Préfixe racine (navigateur)** to scope the browser (e.g. `/Company/`).
   *Required for the standalone app.*
4. Optionally adjust **Extensions ouvrant Nextcloud** (which file types open in
   Nextcloud instead of the inline preview) and the **share presets**.
5. To enable the embedded tab on a project, set its *Nextcloud folder*; tasks
   inherit their project's folder.

Grant users the *Navigateur Nextcloud* access group to let them use the browser
and see the systray launcher.

## Usage

- **Embedded:** open a project or task → *Fichiers Nextcloud* tab.
- **Standalone:** menu **Nextcloud → Fichiers Nextcloud**.
- **Launcher:** the folder icon in the systray opens Nextcloud in a window.

## Limitations

- Uploads are sent base64-encoded over RPC; very large files are not suitable.
- The standalone app cannot embed Nextcloud in an iframe (Nextcloud sends
  `X-Frame-Options: SAMEORIGIN`); the systray launcher opens a real window
  instead.
- Odoo Knowledge article linking is only available when the Knowledge app is
  installed; otherwise files link to the Knowledge Matrix.

## License

LGPL-3. See [`LICENSE`](LICENSE) for the full text.

**Licence note.** The dependency on `project_knowledge_matrix` is BUSL-1.1.
The coupling is at runtime rather than structural — the browser searches and
opens `project.knowledge.item` records through the ORM — so the manifest
dependency could technically be dropped. It is kept deliberately: linking a
Nextcloud file to a knowledge item is a core feature of this browser, not a
side integration, and a build without it would install cleanly while missing
the point.

The LGPL-3 text applies to this module's own source, but a working install
needs the BUSL-1.1 terms on `project_knowledge_matrix`. If you need more than
those terms allow, [talk to us](https://symbifox.com).
