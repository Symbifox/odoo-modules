"""Recompute the stored level of existing key types.

18.0.1.0.6 gave cards and remotes no level; the stored value of the types created before
stayed "single key" (an upgrade does not recompute a stored field whose dependencies changed).
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Type = env["bf.key.type"].with_context(active_test=False)
    types = Type.search([])
    for name in ("depth", "level", "is_master"):
        env.add_to_compute(Type._fields[name], types)
    types.flush_recordset(["depth", "level", "is_master"])
