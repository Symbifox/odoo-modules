# Symbifox Shared Bookmarks (`bf_bookmarks`)

Company, team and personal bookmarks, kept in Odoo and copied into each person's
browser by the *Symbifox Signets* extension. The client portal, the internal
documentation, a team's tools: links that usually live in a welcome email, a
"useful links" document or someone's memory.

The interface is in French; the labels below are quoted as they appear on screen.

## Scopes

| Scope | Who sees it | Who changes it |
|---|---|---|
| Company | every employee of the company | the « Responsable des signets » group (bookmarks managers) |
| Team (department) | the department's members and its manager | the department manager, bookmarks managers |
| Team (project) | the project's followers and its manager | the project manager, bookmarks managers |
| Personal | its owner | its owner (managers see nothing) |

The scope belongs to the **root** of a tree (one root per target); sub-folders and
bookmarks copy that target, so the record rules read it straight off the record.
Project followers include anyone who may read the project and chooses to follow it:
following a project gives access to its team bookmarks. Likewise, when employees may
edit their own department (the HR « Employee Editing » setting, off by default), moving
to a department gives read access to that department's team bookmarks.

Odoo only checks a write rule against the record **before** the write. Every write
that moves a folder or a bookmark is therefore checked again **after** it: without
that, turning a personal folder into a company one would publish it to every
employee. And a bookmark's copied scope fields only ever follow its folder: after any
create or write, from any caller, a bookmark whose scope, company, department,
project, owner or host disagrees with its folder (or its address) is refused. The module's forms
only name what you may read: a form asked to show a bookmark or a folder you cannot
read (through its values, a `default_*` context key or a personal default value) is
refused, and the name of a bookmark or folder you cannot read is never shown: computed
for you, it reads « Signet » or « Dossier ». Titles and folder paths of other people's
personal bookmarks do not leak through forms, nested lines, proposals, or the detailed
access-error message Odoo shows in debug mode.

## In Odoo

- **Bookmarks icon** in the top bar: accent-insensitive search, favorites, recent
  links, and a "+" button that bookmarks the Odoo page you are on. The menu opens
  at once on the last state read in the page, and the server read, which carries the
  access rights, refreshes it as it comes back. Only the first opening of a page
  waits for that read, and hovering the icon starts it early. Nothing is kept in the
  browser beyond the page.
- **Command palette** (Ctrl+K): bookmarks show up among the results, prefixed with
  "Signet :"; Enter opens the link in a new tab.
- **Bookmarks page** (`/odoo/signets`): one card per section, favorites and recent
  links on top, drag and drop to reorder favorites and to file bookmarks where you
  may write. With `bluefox_branding`, it installs as a reduced-scope app on a phone
  (add `bf_bookmarks` to its `bluefox_branding.scoped_app_modules` list).
- **Favorites and recent links are per person**: nobody else, managers included,
  sees what you starred or opened. A favorite or recent link is only shown while you
  may still read its bookmark: moved out of your reach, it disappears from your lists.
- **« Signets » button** on each project (it creates the project's root on first
  use, for whoever may) and on each customer (the bookmarks of their projects).
- **« Copier le lien »**, and **« Proposer à l'équipe »**: a personal bookmark proposed
  to a team or company section goes to its manager, who accepts it (the bookmark is
  created with the manager's rights) or declines it. Once proposed, its title, address
  and note can no longer be changed: the manager decides on what they read.
- **Dead links**: every week, company and team bookmarks are visited (never personal
  ones). A link is dead after **two** failed checks in a row (domain gone, no
  connection, 404 or 410); a 401 or 403 is never dead, since it is usually a login
  page. Addresses that resolve to a private network, and ports other than 80 and 443,
  are never visited, so never declared dead (a name the server cannot resolve at all
  counts as a failed check). When a link dies, an activity goes to the section's manager,
  without an email.
- **Company roots** are created for each main company that has at least one
  employee, with a first bookmark to Odoo itself.
- **« Supprimés depuis le navigateur »** (deleted from the browser): archived, not
  erased, listed, and « Restaurer » puts them back where they were (parent folders
  included), in the browser too.

## Site icons

The server fetches the icon of each host (a scheduled action, with an SSRF guard:
the host is resolved once, every address must be public, the connection is pinned to
the checked address, redirects are followed by hand and re-checked, sizes are capped,
the 100.64.0.0/10 shared range is refused, IPv4-mapped IPv6 addresses are checked as
the IPv4 they carry; only ports 80 and 443) and keeps it
in a per-host cache that users can neither list nor read: the list of hosts would
tell which sites colleagues keep in their personal bookmarks. The cache is shared by
the whole database, so bookmarking a host shows at once whether its icon is already
known.

The menu does not receive the image but its address,
`/bf_bookmarks/icone/<id>/<token>.png`, which the browser keeps in its cache
(`Cache-Control: private, immutable`). The token is signed with the database secret
and bound to the image. Only the bookmarks read, which runs with the person's own rights,
hands out these addresses, and an address changes when its icon changes.

## For the browser extension

- `bf.bookmark.folder.extension_tree()`: one section per visible root, in the order
  company, departments, projects, personal, each with the right to write in it.
- `bf.bookmark.folder.extension_apply(ops)`: creations, edits, moves and deletions
  made in the browser. Each operation has its own savepoint: one refusal does not
  take the others with it. Depth (20 levels) and batch size (500 operations) are
  bounded.

The extension itself is not part of this repository; `bf_policy` distributes it to
managed browsers.

## Addresses

`http` and `https` only, checked on what is stored, whatever the path (form, context
defaults, one2many commands, copy). A company bookmark lands in every employee's browser:
`javascript:` would run on click, and `file://host/share` leaks an NTLM hash to the
named host on Windows.

## Import

« Signets > Importer »: a Netscape-format bookmarks file (the export of Chrome, Edge,
Brave or Firefox, or the nightly backup of the Nextcloud Bookmarks app) into any folder
you may write in, up to 5,000 bookmarks per file. The import can be replayed: a
bookmark already in the same place is not created twice.

## Requirements

Odoo 18 (`hr`, `project`, `mail`). No external Python dependency.

## Tests

`--test-tags /bf_bookmarks`: 107 tests covering the rights per scope, escalation by
move and by the copied scope fields, names leaking through forms, addresses, the extension protocol, import,
favorites and recent links, the SSRF guard, dead links, proposals and the icon
addresses.

## Licence

Business Source License 1.1, converting to LGPL-3.0-or-later on the Change Date. See
`LICENSE`.
