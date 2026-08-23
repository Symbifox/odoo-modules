# Shared session cookie across subdomains (`bf_session_cookie_domain`)

Makes the `session_id` cookie valid for every subdomain of one configured apex
domain, so that reaching the same Odoo through `example.com`, `www.example.com`
and `odoo.example.com` is one session rather than three.

## Why

Serve one Odoo behind several proxy hosts and the browser treats each host as a
separate cookie scope. Each host therefore starts its own sign-in. With OIDC /
OAuth that is worse than an extra click: every sign-in rewrites
`res_users.oauth_access_token`, which is part of `_get_session_token_fields()`,
so the token that every *other* open tab is holding stops matching. Those tabs
raise `SessionExpiredException` on their next request.

The symptom people report is "SSO does not hold" — tabs logging themselves out
at random, with nothing in the identity provider to explain it.

## What it does

Patches `set_cookie` on Odoo's response classes so that, on requests already
addressed to the configured domain, the `session_id` cookie is emitted with
`Domain=.<apex>` instead of being host-only. A same-name `Max-Age=0` cookie is
sent first to evict any host-only `session_id` left from before the module was
installed, which would otherwise shadow the new domain-wide one.

Only the `session_id` key is touched. Other cookies, other domains, and scripted
access (XML-RPC, webhooks) are unaffected.

## Configuration

⚠️ **The module does nothing until you configure it.** A shared session cookie
widens where a session is valid, which is a security-relevant choice, so it is
opted into rather than inferred.

Set the apex domain in Settings › Technical › System Parameters:

| Key | Value |
|---|---|
| `bf_session_cookie_domain.root_domain` | `example.com` — no leading dot, no scheme, no port |

Leave it empty and Odoo's cookie handling is untouched.

The domain is deliberately **not** derived from the request host. Stripping the
leftmost label guesses wrong on names like `odoo.example.co.uk` and would hand
the session to an entire public suffix.

## Scope and caution

- Every host under the configured domain shares the session. Only set it when
  you control all of them.
- The apex itself is included: `example.com` and `*.example.com`.
- Requests on any other domain go through untouched, so one Odoo serving several
  unrelated domains only shares the session on the configured one.

## Requirements

Odoo 18 Community, `web`, `auth_oauth`.

## License

LGPL-3.
