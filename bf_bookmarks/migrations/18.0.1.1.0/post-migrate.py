# -*- coding: utf-8 -*-
"""18.0.1.1.0 : les bases qui avaient déjà le module reçoivent leurs racines
d'entreprise, que l'installation pose désormais d'office."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["bf.bookmark.folder"]._ensure_company_roots()
