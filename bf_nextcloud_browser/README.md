# Nextcloud File Browser

Embedded and standalone WebDAV file browser for Nextcloud, integrated into Odoo
project and task forms. Browse, preview, upload, organise and share files stored
on a Nextcloud instance — without leaving Odoo.

Each person connects **their own** Nextcloud account: the browser acts on their
behalf, sees what Nextcloud lets them see, and every share link, upload and
activity entry carries their name.

- **Author:** Blue Fox Inc.
- **License:** LGPL-3
- **Odoo:** 18.0
- **Depends:** `bf_document_nextcloud_sync`, `project`, `project_knowledge_matrix`

## Features

- **Embedded tab** *Fichiers Nextcloud* on `project.project` and `project.task`
  forms, scoped to each record's Nextcloud folder. The tab only shows once the
  project has a folder.
- **Search the whole tree** (since 18.0.4.1.0): from three characters, the
  filter box searches every folder under the root instead of filtering the
  current one. Names only, without case or accents.
- **Insert a Nextcloud link in a message** (since 18.0.4.1.0): a cloud button
  in the chatter composer opens the browser as a picker; for the chosen file or
  folder, the person picks an internal link or a share made with a preset.
- **Files linked in a chatter** (since 18.0.4.1.0): a cloud button in the
  chatter bar lists the Nextcloud links already cited in that record's
  messages, with their state (file found, share expiry, password), and revokes
  a cited share.
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
  external read-only with expiry and optional password). Since 18.0.4.1.0 the
  share dialog lists the entry's existing shares and revokes them, rows carry
  a badge when a file or folder is shared, and every row copies its internal
  `/f/` link.
- **Knowledge integration** — link a file to a Knowledge Matrix item
  (`project.knowledge.item`) or, where available, an Odoo Knowledge article.
- **Systray launcher** — a toggleable button, carrying the Nextcloud logo,
  that slides the file browser open over the record you are already on. A
  second click, or a click outside, closes it; a button in its header hands the
  same folder over to the standalone app.

## Identity: one Nextcloud account per person (since 18.0.4.0.0)

Up to 18.0.3.x the browser spoke to Nextcloud with the storage configuration's
single account. Every member of the browser group therefore acted as that one
account: Nextcloud's own permissions were never consulted, every share link was
owned by that account, and Nextcloud's audit and activity logs attributed every
action to it. It also only worked for administrators, because the configuration's
connection fields are restricted to `base.group_system`.

Each person now connects their own account through Nextcloud's **Login Flow v2**,
the mechanism Nextcloud's desktop and mobile clients use:

1. the browser shows *Connectez votre Nextcloud*; the button opens a Nextcloud
   window;
2. the person signs in there — with whatever authenticates Nextcloud, single
   sign-on included (e.g. Authentik through `user_oidc`) — and approves the
   access requested by Odoo;
3. Odoo polls Nextcloud, receives an **app password** issued for this device,
   asks Nextcloud which account it belongs to, and stores it encrypted.

Odoo never sees the person's password. The poll token and the app password are
encrypted with the same Fernet key as the configuration's own password
(`NC_DOC_SYNC_FERNET_KEY` or `nc_doc_sync_fernet_key`, kept outside the
database), and no method of the browser returns either of them.

Every URL Nextcloud hands back (login page, poll endpoint, server) is compared
as a string to the configured server address, and anything carrying `\`, `@`,
whitespace or a control character is refused: URL parsers disagree on such
addresses, and the one that checks is not the one that connects. None of the
flow's requests follows a redirect. An app password that Odoo ends up refusing
is revoked on the configured server straight away.

**Whose account gets connected.** Whoever opens the login page approves it, so a
page forwarded to someone else would connect the sender to the recipient's
files. By default the approved Nextcloud account must therefore match the Odoo
user's **login**: the Nextcloud login name or the Nextcloud email address must
equal it, ignoring case. The Odoo email field is deliberately not used, since
people can edit their own; the login only an administrator can change. Otherwise
the connection is refused and the app password revoked. The check is the
configuration's *Exiger l'identifiant de la personne* option; turn it off only
where Odoo logins and Nextcloud accounts share no identifier.

The rule assumes that an Odoo login and the Nextcloud account bearing the same
login name, or the same email address, belong to the same person. Check it on
both sides before relying on it: a Nextcloud user may be allowed to change their
own email address, and a generic login such as `admin` may exist on both
systems in different hands. Changing an Odoo user's login ends that user's
connections (see *Revocation*); a connection is checked once, when it is made.

**Which server gets the token.** A connection remembers the server that issued
its app password, and the password is never sent anywhere else, revocation
included: revoking means sending the token. Changing the configuration's
Nextcloud URL revokes every connection on the old server once the new address
has been accepted, and drops it, including rows removed from the connection
list in the same save; people then connect again on the new one. A connection
whose server no longer matches the configuration is dropped without being sent
anywhere.

**What keeps the configuration's account.** Only the browser sets the per-person
identity (context key `bf_nc_as_user`). The synchronisation crons, the upload
wizard and every other caller of `bf_document_nextcloud_sync` keep the
configuration's account: a cron has no person to act for. A browser call from a
person who is not connected never falls back to it — it asks them to connect.

**Revocation.** A Nextcloud app password is a *permanent* token: nothing ends it
by itself. It ends when:

- the person clicks *Déconnecter* (the token is also deleted on Nextcloud);
- an administrator deletes the row under *Nextcloud → Connexions*
  (same effect);
- the Odoo account is **archived**, or its **login changes** (same effect, for
  all its connections);
- the configuration's Nextcloud URL changes (same effect, for everyone);
  ⚠️ deleting the account outright, deleting the configuration, or removing
  someone from the browser group does not reach Nextcloud: revoke there too;
- the person or an administrator revokes it in Nextcloud
  (*Settings → Security → Devices & sessions*, listed as
  `Symbifox Odoo (<company>)`); the browser then asks to reconnect.

⚠️ Disabling someone in the identity provider (Authentik…) does **not** end an
app password already issued. Archive the Odoo account, or revoke the device in
Nextcloud, when someone leaves.

**Paths are read in each person's Nextcloud.** The root prefix and each
project's folder are resolved in the connected person's own file tree. A folder
shared to someone appears at the **root** of their tree under its share name,
not under the sharer's path. For a team, share the top folder (e.g. the whole
`/Company/` folder) or use a Nextcloud *Team folder* (Group folders), so the
same path exists for everyone. When it does not, the browser says *Ce dossier
est introuvable dans votre Nextcloud, ou il ne vous est pas partagé* rather than
a WebDAV error.

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
- the standalone app refuses to operate unless a meaningful root prefix is set;
- the Nextcloud call itself is made with the caller's own app password (see
  *Identity* above), so Nextcloud applies its own permissions on top of these.

Preview responses are served with `X-Content-Type-Options: nosniff` and a CSP
forbidding scripts, form submission and a `<base>` override. Only an allowlist is
rendered as itself: PDF, common raster images, audio, video and plain text, all
but PDF in a sandboxed (opaque-origin) document. Anything else that is text,
HTML, SVG and every XML-based type included, is shown as its source; anything
else is downloaded. Rendered from the Odoo origin, a document type could draw a
fake sign-in page or send the reader elsewhere, scripts or not, and browsers
render far more XML types as documents than any list of dangerous types names. Share-link permissions are derived server-side; HTML inserted into
Knowledge articles is escaped.

Connection rows (`bf.nc.user.credential`) are written only by server code.
Through RPC a person reads their own row and nothing else, an administrator reads
and revokes everyone's, and nobody can create or edit one. The encrypted secrets
are system-only fields, and the key lives outside the database, which protects
them in a database dump or a backup. It does not protect them from an Odoo
system administrator, who can run code on the server and could therefore act
as any connected person on Nextcloud. The per-person context key can
only ever name the caller: a client that sends it for someone else is refused.

The top-level *Nextcloud* app and the systray button only appear when the default
configuration can actually be browsed (active, with a root prefix other than
`/`) — for administrators too. Until then, administrators create or fix that
configuration from *Knowledge → Configuration → Nextcloud Documents*.

## Search, links and shares (since 18.0.4.1.0)

- **Search** runs a WebDAV `SEARCH` as the person, scoped to the record folder
  or to the root prefix, newest first, 100 results at most. The scope is sent
  unencoded (Nextcloud 34 answers 404 to an encoded one); the term is
  XML-escaped and its `%` and `_` wildcards are escaped.
- **Internal link or share, chosen each time.** An internal link (`/f/<id>`)
  opens only for someone who can already see the file in Nextcloud and
  creates nothing. A share follows a preset; the dialog describes the rules it
  will really get (a preset without expiry takes the configuration's default,
  and the configuration's password switch adds a password). A share password
  is never inserted in the message: it is shown apart, to be sent separately.
  The root of the browser or of a record is never shared publicly.
- **Files linked in a chatter** are read again from the record's messages each
  time (1,000 newest messages, 200 links): nothing is stored. Internal links
  are resolved by file id in the person's whole Nextcloud account (in practice
  most cited files live outside the browser root), shares by the person's own
  share list, each share read alone for its expiry. A cited share can be
  revoked from there when Nextcloud lets the person delete it.
- **Revocation from the browser** stays within the browser root.
- **Other addresses of the same Nextcloud** go in *Autres adresses de ce
  Nextcloud* on the configuration, so links written with them are recognised.

## Configuration

1. Open **Nextcloud → Configuration** (admin only).
2. On the Nextcloud configuration record set the Nextcloud base URL,
   WebDAV path, service-account user and app password.
3. Set **Préfixe racine (navigateur)** to scope the browser (e.g. `/Company/`).
   *Required for the standalone app.*
4. Optionally adjust **Extensions ouvrant Nextcloud** (which file types open in
   Nextcloud instead of the inline preview) and the **share presets**.
5. To enable the embedded tab on a project, set its *Nextcloud folder*; tasks
   inherit their project's folder.

Grant users the *Navigateur Nextcloud* access group to let them use the browser
and see the systray launcher. Each of them connects their own Nextcloud account
the first time they open the browser; the list of connections is under
*Nextcloud → Connexions* (administrators).

Nextcloud side: app passwords must be allowed (`auth_can_create_app_token`,
enabled by default), and the folders the browser points at must exist at the
same path in each person's tree (see *Identity*).

## Usage

- **Embedded:** open a project or task → *Fichiers Nextcloud* tab.
- **Standalone:** menu **Nextcloud → Fichiers Nextcloud**.
- **Launcher:** the Nextcloud logo in the systray opens the file panel over
  the current page.

## Limitations

- Uploads are sent base64-encoded over RPC; very large files are not suitable.
- Nextcloud cannot be embedded in an iframe (it sends
  `X-Frame-Options: SAMEORIGIN`), so the browser reads the files over WebDAV
  and renders them itself. Reaching the real Nextcloud is a per-file escape
  hatch — *Open in Nextcloud*, and office documents, which open in a tab.
- Odoo Knowledge article linking is only available when the Knowledge app is
  installed; otherwise files link to the Knowledge Matrix.
- Search covers file and folder names, not their content.
- A name carrying a percent escape (`A%2012.odt`) is listed but refused for any
  action, with a message: the path helpers url-decode once more on their way
  out, so such a name would address another file (and did, silently, before
  18.0.4.1.0). Such names do occur in real accounts.
- The cloud button is in the chatter's quick composer, not in the full-page
  email composer.
