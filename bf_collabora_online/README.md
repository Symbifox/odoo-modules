# Collabora Online, Blue Fox fixes (`bf_collabora_online`)

The `collabora_odoo` connector is published by Collabora Productivity under
MPL-2.0, and its own README says it has not had a security review yet. We keep it
**untouched** so it keeps receiving upstream updates, and this module layers on
top the three defects found by reading it line by line.

## 1. The Edit button stops lying

Upstream, `collabora.odoo.can_write_doc` returns a **JSON string**, while the
JavaScript reads `result?.can_write`, an object attribute. And the client-side
`canWrite` is `async`, so the template's `t-if` receives a promise, which is
always truthy.

The result: the edit button appears on every office attachment, including the
ones the person cannot write.

**This is not a vulnerability.** The `/collabora_odoo/frame/<id>/write`
controller re-checks `has_access('write')` and falls back to read-only. It is a
button that promises what it will not deliver.

The fix adds `bf.collabora.helper.pieces_modifiables(ids)`, which answers for a
whole thread in one call, and makes `canWrite` synchronous. Along the way it
replaces `check_access`, which **raises** instead of returning false, with
`has_access` plus `ir.attachment.check`, which is the gate the model itself
applies on write.

## 2. `IsAdminUser` stops being true for everyone

Upstream hardcodes `'IsAdminUser': True` in the WOPI `CheckFileInfo` response,
for any user. The fix puts the real value there, read from the **token**
identity: the route is `auth='public'`, so `request.env.user` is the public user
and not the person.

The upstream controller is not copied. It produces the whole response, and only
the offending value is replaced. Copying its forty lines would amount to forking,
and every upstream fix would then have to be redone by hand, including the ones
that touch access.

## 3. Discovery stops being re-fetched on every open

Upstream calls `discover.collabora_url` every time a document opens: a
**synchronous** HTTP request to the Collabora server's `/hosting/discovery`
before the iframe even appears. That file only changes when the server is
upgraded.

The fix replaces the function inside the upstream module (the controller
resolves it at call time, so replacing the attribute is enough) with one that
keeps the answer in memory.

⚠️ **The cache carries a time to live, not just a manual flush.** The URL that
discovery returns embeds Collabora's build number
(`/browser/<hash>/cool.html`): kept forever, it would point at a path that
disappeared on the first server upgrade. The error window is bounded by the TTL,
which is configurable, and a "Clear the cache now" button lives in Settings →
Collabora Online for when you do not want to wait for it.

## Settings

Settings → Collabora Online. One setting: `bf_collabora.decouverte_ttl`, in
seconds, 900 by default. `0` restores the upstream behaviour.

The connector's own settings (server URL, WOPI host URL, JWT secret, token TTL)
stay on its own page.

## What this module does not do

It does not touch the WOPI protocol, the access control, or upstream's
`X-COOL-WOPI-Timestamp` conflict guard, all of which are correct. It is not a
version history either: `bf_attachment_version` covers that, for both editors.

## On the Collabora server side

The Odoo host must appear in `coolwsd.xml`'s `alias_groups` **and** in the
container's `domain` variable, or Collabora refuses the WOPI session. That change
requires restarting the container.

## Licence

BUSL-1.1, converting to LGPL-3.0-or-later on 2030-09-02. Internal production use
is free; providing it as a product or service to third parties requires a
separate agreement.
