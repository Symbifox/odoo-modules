# Symbifox — Blue Fox OS Policy, Live USB Key (`bf_policy_live`)

The Odoo side of the **Blue Fox OS live USB key**: a key that boots any PC into a
Blue Fox OS session, opened by scanning a QR code with your phone. This module
escrows the keys of the persistent profiles kept on the USB key, and gives the
key's browser a one-time Odoo sign-in so nobody types a password on a borrowed PC.

It extends [`bf_policy`](../bf_policy), the group-policy plane of Blue Fox OS, and
reuses its identity checks and its escrow key.

## Why

A live USB key is meant to be lost. Whatever it carries must be useless to whoever
finds it, and the person using it should not have to type secrets on a machine they
do not trust.

- **Profiles**: at sign-in the person chooses an ephemeral session or their
  **persistent profile**, one LUKS2 file per person on the key. The key that
  decrypts that file is **never on the USB key and never typed**. It is drawn here
  (32 random bytes), encrypted with `bf_policy`'s escrow key (from
  `BF_POLICY_ESCROW_KEY` or `bf_policy_escrow_key` in `odoo.conf`, never from the
  database), and handed to the live key after the QR sign-in. Losing the USB key
  gives away no profile.
- **Odoo sign-in**: after the QR code, the browser on the key has no session yet.
  The key asks for a one-time link and opens it: Odoo opens signed in, nothing typed.

## Profile key API

`POST /api/v1/policy/live/profile-key`

- `Authorization: Bearer <Authentik access token>`. The token must have been issued
  to the live key's client (`aud` or `azp` = the policy's **Client OIDC de la clé live** field,
  `blue-fox-os-live` by default), **even when the organisation's audience check is
  turned off**, and it must be a JWT carrying `sub`. A token obtained by another
  application of the same Authentik (Nextcloud, the installer…) is refused.
- The person is resolved exactly as for `/api/v1/policy/me`: Authentik's userinfo
  endpoint, one claim compared with the login, and the person must be allowed by the
  policy (no portal, archived or out-of-company account).
- The token's `amr` claim must contain `mfa` (since 18.0.1.3.0): the decrypted
  profile holds the browser's cookies, so the key is not served on a password alone.
- Body: `{"create": true}` or `{"create": false}`. Only the boolean `true` creates.

| Response | Meaning |
|---|---|
| `200 {"key": "<base64>", "created": true}` | profile created: format the LUKS file with this key |
| `200 {"key": "<base64>", "created": false}` | existing profile: open it with this key |
| `400` | unreadable body |
| `401` | no token, token refused, token of another client, or without `sub` |
| `403 {"error": "second factor required"}` | the token's `amr` does not contain `mfa` |
| `403 {"error": "revoked"}` | revoked profile: discard the LUKS file, nothing will be recreated |
| `403 {"error": "identity mismatch"}` | another Authentik account than the one that created the profile |
| `403` (other) | person not allowed by the policy |
| `404 {"error": "no profile"}` | no profile and `create` is not true |
| `409` | two simultaneous creations: ask again |
| `503 {"error": "escrow unavailable"}` | escrow not configured: nothing is created, offer the ephemeral session |
| `500 {"error": "profile key unreadable"}` | the escrowed key no longer decrypts (escrow key changed, damaged record) |

As for every `bf_policy` route, a request whose host matches no policy is refused
before any of this (`404` / `503`, see `bf_policy`).

Responses carry `Cache-Control: no-store`. Odoo's log records who, when and from
which IP, never the key.

The profile pins the Authentik `sub` that created it, so another account that later
takes over the same e-mail address is refused. If the identity provider changes the
way it computes `sub`, **Réépingler le compte** (re-pin the account; escrow right) lets the next request record the new one.

## One-time Odoo sign-in (since 18.0.1.1.0)

`POST /api/v1/policy/live/login-link`

- Same checks as `profile-key` (shared code): live client token carrying `sub`,
  person allowed by the policy, JSON object body of 1 KB at most. Active internal
  users only.
- **The token's `amr` claim must contain `mfa`** (since 18.0.1.3.0). The link skips
  Odoo's own second factor, so Odoo checks that Authentik required one. The live
  key's client is public (device flow, no secret): anyone can run that flow without
  the USB key, so this check cannot be left to the key. Authentik copies `amr` from
  the ID token into the access token.

| Response | Meaning |
|---|---|
| `200 {"url": "<root>/odoo?bfos_login=<token>", "expires_in": 300}` | open it in the browser within 5 minutes |
| `400`, `401`, `403` | as for `profile-key`, plus `403 {"error": "internal users only"}` and `403 {"error": "second factor required"}` |
| `429 {"error": "too many requests"}` | more than 20 links in the hour for this person |

`GET /odoo?bfos_login=<token>` (also `/odoo/<path>`, `/web` and `/scoped_app/<path>`, the
routes of Odoo's web client)

The link goes through Odoo's own web client rather than a separate route: Chromium
browsers group an app window by the host and path of the URL that opened it, so the
first window lands under the pinned Odoo icon (`https://<host>/odoo`). Without
`bfos_login`, these routes behave exactly as in stock Odoo.

- Valid, unused, unexpired token, user still internal and active: the Odoo session is
  opened and the browser is redirected to the same address **without** `bfos_login`
  (other parameters kept). **Odoo's own second factor is not asked again** (TOTP, or
  the e-mailed code enforced for employees): the link was only issued for a token
  whose `amr` contains `mfa` (see above).
- **A link only opens on a navigation started by the browser itself** (since
  18.0.1.3.0): the request must carry `Sec-Fetch-Site: none`, which is what a
  browser sends when it is launched on an address (the live key starts Brave with
  `--app=<link>`) or when the address is typed. A link followed from an e-mail, a
  chat message or another page, including at the end of a redirect chain such as
  `/web/session/logout?redirect=<link>`, arrives as `cross-site` or `same-origin`
  and is refused without being consumed. Otherwise a member could draw a link for
  their own account and have a colleague's browser signed into it.
- **A link never replaces another user's session** (since 18.0.1.3.0). If the browser
  is already signed in as someone else, or halfway through someone else's sign-in
  (second factor pending), the link is not consumed and that session is kept.
- Any other case (unknown, expired, already used, user turned portal or archived):
  redirect to `/web/login` when the browser has no session, otherwise back to the
  same address with the current session kept; no detail is shown, the reason goes
  to the log.

A link is not bound to the browser that requested it: someone who pastes another
person's still-valid link into their own address bar is signed in as that person.
The link lives five minutes, is used once, and is only handed to the live key over
its authenticated API.

The token (32 random bytes) is **never stored**: only its sha256, with the person,
the expiry, the requesting IP and the opening IP. It is consumed by one atomic
`UPDATE`: of two simultaneous openings, only one wins. Records older than a day are
purged daily (`@api.autovacuum`).

The custom credential (`type` `bf_live_link`) is accepted by
`res.users._check_credentials` **only while a link is being opened through `/odoo`**
(a Python context variable that no RPC call or parameter can set). `/web/login` lets
the client choose `type`, but carries neither the marker nor the `bf_live_token` key.

⚠️ The HTTP access log (werkzeug, reverse proxy) records the full address, hence the
token. By then it is already consumed or refused, but a still-valid link could be read
there if it was refused for a reason other than expiry. Keep those logs at the same
protection level as Odoo's.

### Browser session in Authentik (since 18.0.1.2.0)

Optional. When the system parameter `bf_policy_live.authentik_nav_url` holds the
address of an Authentik flow (an `https` URL without a query string), and the link
carries `bfos_auth=<code>` (43 base64url characters), Odoo sends the browser to that
flow **right after consuming the link**, with `?c=<code>`. The flow, not Odoo,
verifies the code and opens the person's Authentik session in the browser, then
returns to Odoo. Odoo never relays a code for a session it did not just open. Unset
or malformed, the parameter turns the relay off.

That flow is **not part of this module**. Whoever sets it up must make it verify the
code against a fresh access token of the live key's client, use it once, require
`mfa`, and refuse to open a session in a browser that already has one.

## Revocation is a remote wipe

**Policy › Profils de clé live › Révoquer** (revoke; the "Can reveal disk
passphrases" escrow right of `bf_policy`,
which Odoo administration does not imply) **erases the escrowed key**: the encrypted
profile left on the USB key becomes unreadable for everyone, the host included. The
revoked record stays, so the live key receives a 403 instead of creating a new one.
To lift a revocation, delete the record (same right): the person starts over with a
new profile.

⚠️ The wipe holds against whoever has the USB key, not against a **database backup
taken before the revocation**: the encrypted key is still there, and it opens with
the escrow key. Protect backups and the escrow key separately, as for disk passphrases.

Removing the person from the policy (provisioning mode, archiving, portal) also stops
their key from being served, without erasing it.

## Access

- `bf_policy` managers (`base.group_system`): read the profile records, never the key.
- The escrow right: read, revoke, delete. No button reveals a key: nobody needs it,
  the live key asks for it itself. The **Policy** menu itself is reserved to
  `base.group_system`, so revoking from the interface takes both rights.
- This separation holds against the interface and RPC, not against a system
  administrator who can run code as superuser (a server action, a shell): such a
  person can reach the escrowed keys, as with `bf_policy`'s disk passphrases.
- Ordinary internal users and portal users: no access.
- Sign-in links: `base.group_system` reads the records (who, when, from where); no
  write, no stored token.

## Languages

The interface (menu entries, fields, buttons, messages) is written in French, the
language of the module's source; the API's JSON error codes are English.

## Requirements

- Odoo 18.0
- [`bf_policy`](../bf_policy) (same repository), with its escrow key configured
- An Authentik (or other OIDC) provider with a public device-flow client for the
  live key, named in the policy's **Client OIDC de la clé live** field; it must differ from
  the installation and Nextcloud clients
- Authentik must put `mfa` in `amr` only for a real second factor: its validation
  stage must **not** let someone who only has the password enrol a new device
  during sign-in (`not_configured_action = configure`). Expect `403 second factor
  required` for a passkey-only sign-in (`amr` = `hwk`), a federated source, or a
  token refreshed after the Authentik session ended (no `amr` any more)

## Licence

Business Source License 1.1, see `LICENSE`. Change Date: 2030-10-25, after which
this version converts to LGPL-3.0-or-later.

## Changelog

- **18.0.1.3.0**: the profile key and the sign-in link require `mfa` in the token's
  `amr`, checked by Odoo; a link only opens on a browser-initiated navigation
  (`Sec-Fetch-Site: none`) and never replaces another user's session, even a
  half-open one.
- **18.0.1.2.0**: optional relay to an Authentik flow that opens the browser's
  Authentik session right after the sign-in link (`bf_policy_live.authentik_nav_url`).
- **18.0.1.1.0**: one-time Odoo sign-in link for the live key.
- **18.0.1.0.0**: escrow of the live key's persistent profile keys, revocation as
  remote wipe.
