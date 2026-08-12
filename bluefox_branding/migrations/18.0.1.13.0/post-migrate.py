"""Re-apply Blue Fox branding — added calendar event templates (invitation, changedate, reminder, update)."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
