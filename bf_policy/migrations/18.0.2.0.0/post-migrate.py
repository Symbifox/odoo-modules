"""Fold the legacy bf.zerotouch.tenant config into bf.policy.org (18.0.2.0.0).

The zero-touch per-tenant config (OCI image, secondary locale, X layouts, OIDC
device/token endpoints, client id, slug) used to live in its own
bf.zerotouch.tenant model. It now lives on bf.policy.org so one Policy row fully
describes a tenant. This migration copies each legacy row onto the matching org.

bf_zerotouch_install depends on bf_policy, so bf_policy is upgraded FIRST and the
legacy table is still present and populated here. The table itself is dropped by
bf_zerotouch_install's own 18.0.4.0.0 migration, which runs afterwards.

Matching: by bare domain (case-insensitive). A legacy row and its org
both carry the same domain (e.g. ``example.com``). If exactly one legacy row and one
org exist, they are paired even if a domain is blank (the single-tenant
case). Only blank target fields are filled, so an admin edit made after
an interrupted upgrade is never clobbered. Idempotent.
"""
from odoo import SUPERUSER_ID, api

# legacy bf_zerotouch_tenant column -> bf.policy.org field
_COPY = {
    "oci_image_ref": "oci_image_ref",
    "addsupport": "addsupport",
    "keyboard_x": "keyboard_x",
    "authentik_device_url": "authentik_device_url",
    "authentik_token_url": "authentik_token_url",
    "oidc_client_id": "oidc_client_id",
    "slug": "slug",
    # legacy display name (e.g. "Example Inc.") — kept distinct from the company
    # record name (e.g. "Example") so the OS tenant label is preserved exactly.
    "name": "tenant_name",
    # locale/keymap/timezone already exist on the org with the same values;
    # only backfill them if the org somehow has them blank.
    "lang": "locale",
    "keyboard_vc": "keymap",
    "timezone": "timezone",
}


def _norm(domain):
    d = (domain or "").strip().lower()
    return d[len("www."):] if d.startswith("www.") else d


def migrate(cr, version):
    # Legacy table may already be gone on a re-run — nothing to do then.
    cr.execute("SELECT to_regclass('public.bf_zerotouch_tenant')")
    if not cr.fetchone()[0]:
        return

    cols = ["domain"] + list(_COPY.keys())
    cr.execute("SELECT %s FROM bf_zerotouch_tenant" % ", ".join(cols))
    legacy = [dict(zip(cols, row)) for row in cr.fetchall()]
    if not legacy:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Org = env["bf.policy.org"]
    orgs = Org.search([])

    # Single legacy row + single org: pair them regardless of domain.
    single = len(legacy) == 1 and len(orgs) == 1

    for src in legacy:
        if single:
            org = orgs
        else:
            target = _norm(src["domain"])
            org = orgs.filtered(lambda o: o._effective_domain().lower() == target)
            if not org:
                continue
            org = org[:1]
        vals = {}
        for col, field in _COPY.items():
            value = src.get(col)
            if value and not (org[field] or "").strip():
                vals[field] = value
        if vals:
            org.write(vals)
