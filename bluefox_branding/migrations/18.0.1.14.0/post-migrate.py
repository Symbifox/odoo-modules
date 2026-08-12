"""Re-apply Blue Fox branding — calendar templates aligned on bf_meeting shell.

Drops the orange accent and the legacy footer for calendar event templates,
matching the palette and the compact header used by bf_meeting.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
