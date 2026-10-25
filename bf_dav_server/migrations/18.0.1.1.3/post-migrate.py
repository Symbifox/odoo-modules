# Part of bf_dav_server. See LICENSE file for full copyright and licensing details.
"""18.0.1.1.3: the French catalogue is reloaded over what the database holds.

A module update never overwrites a translation already in the database: a
fresh install of 18.0.1.1.x kept the old French label of the field that
titles the Calendar app's filter section ("Agenda CalDAV"), and a sentence
whose English changed keeps its old French. The module's catalogue is the
reference for its own terms."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed() if code.startswith("fr")]
    if langs:
        env["ir.module.module"]._load_module_terms(["bf_dav_server"], langs, overwrite=True)
