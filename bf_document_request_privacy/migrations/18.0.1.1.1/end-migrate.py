"""18.0.1.1.1, `end` step: the French catalogue is reloaded, overwriting.

An update never overwrites an existing translation: without this step, a
database would keep the former French labels (the vocabulary was made
consistent in this release: "pièces", "échéance", no dashes). The `noupdate`
data keeps the values the organisation has: the catalogue never replaces an
existing key of a `noupdate` record, even when overwriting.
"""

from odoo import SUPERUSER_ID, api

MODULE = "bf_document_request_privacy"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms([MODULE], langs, overwrite=True)
