"""Backfill bf.policy.org.domain for rows that predate multi-tenant routing.

The `domain` field is new in 18.0.1.1.0 (the policy-side mirror of the
zero-touch host->org routing). Existing rows
have no domain yet, so derive it from the company website using the model's own
`_domain_of` helper. A leading ``www.`` is stripped: the routing domain must
equal the bare host the operator types at the GRUB zero-touch entry (the same
value carried by bf.zerotouch.tenant.domain, e.g. ``example.com``),
not the marketing ``www.`` website. Idempotent: only touches rows where domain
is null/blank. Until a second org exists, the controller's single-org fallback
covers an unmatched host, so a null domain is harmless in the interim; this just
makes the row explicitly matchable the moment a second tenant is added. Admins
can override the value in Policy > Org Defaults.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Org = env["bf.policy.org"]
    for org in Org.search([("domain", "in", (False, ""))]):
        derived = Org._domain_of(org.company_id)
        if derived.startswith("www."):
            derived = derived[len("www."):]
        if derived:
            org.domain = derived
