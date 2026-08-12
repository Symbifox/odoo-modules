"""1.3.0: rebranded bilingual emails, unsubscribe link, confirmation codes.

The mail templates and the default survey are noupdate=1: a plain -u never
refreshes them on existing databases, so this migration re-applies the
current FR sources and writes the en_CA translations (same shared routine
as the fresh-install hook). Also backfills complaint confirmation codes.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bf_cx.i18n_content import apply_cx_i18n

    apply_cx_i18n(env)
