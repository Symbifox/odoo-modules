"""Create missing search config records (post_init_hook missed them)."""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bf_universal_search.hooks import post_init_hook

    post_init_hook(env)
    _logger.info("bf_universal_search: migration created search config records")
