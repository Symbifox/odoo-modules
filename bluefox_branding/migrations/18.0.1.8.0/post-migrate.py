"""Re-apply Blue Fox branding in all active languages.

v1.7.0 only wrote en_US; fr_CA kept old Odoo defaults.
This migration re-runs post_init_hook which now writes in all languages.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
