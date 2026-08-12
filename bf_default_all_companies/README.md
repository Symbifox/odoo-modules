# BF Default All Companies

Pre-selects every allowed company in Odoo's multi-company switcher when no `cids` cookie is set. Without this, Odoo's stock behaviour falls back to the user's `company_id` alone on first login or after logout — even for users that have access to several companies.

## Problem

Odoo persists the active set of companies (the bubbles toggled on in the top-right switcher) in a browser cookie called `cids`. When the cookie is missing, the client-side `companyService` defaults to `[user_companies.current_company]`, i.e. the single record pointed to by `res.users.company_id`.

That cookie disappears in three ordinary situations:

1. First-ever login on a given browser profile.
2. After every logout — `web/models/ir_http.py::_post_logout` explicitly clears it (`request.future_response.set_cookie('cids', max_age=0)`).
3. After the cookie expiry (~24 days idle).

For a user with access to several companies (e.g. main operating co + holding + pre-incorporation co), this means re-toggling each one in the switcher every time the cookie is wiped. There is no out-of-the-box server-side setting to default to "all companies on".

## Fix

A single JS file shipped in `web.assets_backend` runs at webclient bootstrap, before `companyService.start`:

```js
const allowed = session?.user_companies?.allowed_companies;
if (allowed && !cookie.get("cids")) {
    const ids = Object.keys(allowed).map(Number);
    if (ids.length > 1) {
        cookie.set("cids", ids.join("-"));
    }
}
```

When the cookie is missing, it is seeded with every company in `session.user_companies.allowed_companies` (which is itself derived from `request.env.user.company_ids` server-side). The standard `companyService` then reads the cookie, validates each id against the same allowed set, and toggles them all on.

The seed only fires when `>1` company is allowed, so single-company users are completely unaffected.

User choices made via the switcher during a session keep working as before: as soon as the user toggles companies on or off, `companyService.setCompanies()` writes the new `cids` value to the cookie and our seed no longer fires until the cookie is cleared again. In other words, **explicit user choice always wins**; the seed only kicks in on the "blank slate" path.

## Install

Ship the module in the addons tree and install it like any other:

```python
import xmlrpc.client
models = xmlrpc.client.ServerProxy("https://example.com/xmlrpc/2/object")
models.execute_kw(DB, UID, KEY, "ir.module.module", "update_list", [])
mod = models.execute_kw(DB, UID, KEY, "ir.module.module", "search",
                        [[("name", "=", "bf_default_all_companies")]])
models.execute_kw(DB, UID, KEY, "ir.module.module", "button_immediate_install", [mod])
```

No data files, no models, no migrations. The asset is included in the backend bundle automatically.

## Configuration

None. The behaviour is intentionally global: any internal user with access to more than one company gets the full set selected by default. There is no per-user toggle — if you need to opt out for a specific user, leave their `company_ids` set to a single record.

## Verification

After installing:

1. Log in as a user with access to several companies.
2. Open DevTools → Application → Cookies, locate `cids`. It should already list every allowed company id joined by `-` (e.g. `1-3-5`).
3. Top-right switcher should show every company toggled on.

To test the "blank slate" path on a browser that already has a stale cookie:

```js
// in DevTools console
document.cookie = "cids=; max-age=0; path=/";
location.reload();
```

The cookie is repopulated with all allowed companies before the switcher renders.

## What this module does NOT do

- It does **not** give a user access to companies they were not already entitled to. Allowed companies still come from `res.users.company_ids` and access rules apply unchanged.
- It does **not** override an explicit user choice. Once the user toggles companies in the switcher, that choice is persisted in the cookie until logout.
- It does **not** change the *default* company (`current_company`) — that stays as `res.users.company_id`. New records still get created in the default company unless the user picks another one.
- It does **not** fire for portal / public / single-company users (the `>1` check makes the seed a no-op).
- It does **not** alter session/auth tokens, sudo, ORM access checks, or any server-side multi-company logic.

## Security model

The module reads from `session` (a public, already-rendered object describing the *current* user's own permissions) and writes only the `cids` cookie. The Python server still validates every id in `cids` against `request.env.user.company_ids` on each request — see `web/models/ir_http.py::_sanitize_cookies` and `companyService.computeActiveCompanyIds`. Any attempt to forge `cids` with company ids the user is not entitled to is silently dropped server-side, so this seed cannot widen access; it can only pre-toggle ids that were already allowed.

## Related

- `web/static/src/webclient/company_service.js` — upstream `cids` resolution and `companyService` API.
- `web/models/ir_http.py` — `session_info` exposes `user_companies.allowed_companies` and `_post_logout` wipes the cookie.

## License

LGPL-3.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
