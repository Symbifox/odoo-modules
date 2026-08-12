"""Apply Blue Fox branding to all noupdate=True mail templates.

Since post_init_hook only runs on first install, this migration handles
the upgrade path for existing installations.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Import and run the same logic as post_init_hook
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
