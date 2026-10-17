"""Reload the module's catalogues over what is in the database, at every version change (0.0.0).

An upgrade never overwrites a translation that already exists: a renamed label keeps its old
French on bases installed earlier ("3 levels" in 18.0.1.0.6, "Holder leaving" in 18.0.1.0.10).
The module ships no translatable data a customer would edit, so overwriting is safe.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = env["res.lang"].search([("active", "=", True), ("code", "!=", "en_US")]).mapped("code")
    if langs:
        env["ir.module.module"]._load_module_terms(["bf_key_management"], langs, overwrite=True)
