"""Re-apply template branding after layout architecture change.

Updates contract template email_layout_xmlid to point to the new
standalone BF layout instead of the Odoo standard one.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    # Re-run the hook to update all noupdate=True templates
    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
