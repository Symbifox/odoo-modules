# -*- coding: utf-8 -*-
"""Le crochet d'installation ne rejoue pas sur une mise à jour : on l'appelle ici.

Sans cela, une base où `bf_outreach` était déjà installé n'aurait jamais ses
cibles dans la recherche universelle.
"""
from odoo.addons.bf_outreach.models.post_init import register_universal_search


def migrate(cr, version):
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    register_universal_search(env)
