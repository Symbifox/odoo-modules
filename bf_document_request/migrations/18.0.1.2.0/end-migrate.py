"""18.0.1.2.0, `end` step: the labels move from a French source to an English one.

Odoo never translates to en_US, the source language: as long as the source was
French, a user set to English read this module in French. The source is now
English and ``i18n/fr_CA.po`` carries the French.

An update never overwrites an existing translation: this module's catalogue is
therefore reloaded here, overwriting, so that a French key left by the former
source gives way to the catalogue's French.

The `noupdate` data (the starter catalogue) is not touched: its records keep the
values the organisation has, in every language. The pre-migration gave each
installed language a key, and the catalogue never replaces an existing key of a
`noupdate` record, even when overwriting.
"""

from odoo import SUPERUSER_ID, api

MODULE = "bf_document_request"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms([MODULE], langs, overwrite=True)
