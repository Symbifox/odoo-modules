"""Turn the Ctrl+K instance default on for databases that predate it.

18.0.2.1.0 shipped the setting off everywhere, so that an upgrade changed
nobody's keyboard habit. That caution has served its purpose: the universal
search is what the palette is for, and the native command list stays one
Backspace away. From 18.0.2.2.0 the feature is on out of the box, and this
migration brings the existing databases in line with a fresh install.

⚠️ It only fills an ABSENT parameter. An administrator who has already been to
Settings — to switch it on, or to switch it off — keeps that decision, and so
does every user who chose something in their own preferences (a user's choice
beats the instance default in both directions).
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_universal_search.hooks import enable_ctrl_k_star_default

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if enable_ctrl_k_star_default(env):
        _logger.info("Universal search: Ctrl+K instance default turned on")
    else:
        _logger.info("Universal search: Ctrl+K instance default already decided")
