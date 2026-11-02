"""18.0.1.2.0, `end` step: the labels move from a French source to an English one.

Odoo never translates to en_US, the source language: as long as the source was
French, a user set to English read this module in French. The source is now
English and ``i18n/fr_CA.po`` carries the French.

An update never overwrites an existing translation: this module's catalogue is
therefore reloaded here, overwriting, so that a French key left by the former
source gives way to the catalogue's French. The `noupdate` data keeps the
values the organisation has: the catalogue never replaces an existing key of a
`noupdate` record, and the names and plain-language summaries set aside by the
`post` step are put back as they were.
"""

from odoo import SUPERUSER_ID, api

MODULE = "bf_securetransfer_privacy"
KEEP = "_bf_keep_bf_securetransfer_privacy_purpose"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms([MODULE], langs, overwrite=True)
    env.flush_all()
    cr.execute("SELECT to_regclass(%s)", (KEEP,))
    if cr.fetchone()[0]:
        cr.execute(f"""
            UPDATE privacy_purpose p
               SET name = k.name,
                   plain_language_summary = k.plain_language_summary
              FROM "{KEEP}" k
             WHERE k.id = p.id
               AND (p.name IS DISTINCT FROM k.name
                    OR p.plain_language_summary IS DISTINCT FROM k.plain_language_summary)
        """)
        cr.execute(f'DROP TABLE "{KEEP}"')
    env.invalidate_all()
