# -*- coding: utf-8 -*-
"""Give the landing default to tenants that already carried the module.

``data/bf_home_data.xml`` is marked ``noupdate="1"``, and that is the right
call: a default the operator later removes must not reappear at every upgrade.
The consequence is that its ``<function>`` runs in *init* mode only. On a
tenant where bf_home was already installed, the upgrade loaded the file, skipped
the function, and the landing default silently never existed — the module went
on adding a menu and nothing else, which is the exact gap 18.0.1.1.0 set out to
close. A fresh install worked, so the omission was invisible from the bench.

Runs once, and only when nobody has expressed an opinion yet.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return                      # fresh install: the data file already did it

    env = api.Environment(cr, SUPERUSER_ID, {})
    action = env.ref("bf_home.bf_home_action", raise_if_not_found=False)
    if not action:
        _logger.warning("bf_home: action d'accueil introuvable, défaut non posé")
        return

    field = env["ir.model.fields"]._get("res.users", "action_id")
    if not field:
        return
    if env["ir.default"].search_count([("field_id", "=", field.id)]):
        # Somebody already set a home action default — theirs, not ours.
        _logger.info("bf_home: un défaut d'action d'accueil existe déjà, laissé tel quel")
        return

    env["ir.default"].set("res.users", "action_id", action.id)
    _logger.info("bf_home: écran d'accueil posé comme défaut pour les nouveaux utilisateurs")
