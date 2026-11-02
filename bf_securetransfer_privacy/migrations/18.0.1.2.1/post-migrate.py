"""18.0.1.2.1: the delivered register texts take their French version on a
French-speaking company, when they still hold the English source this module
delivered (a new database installed in 18.0.1.2.0). See hooks.py."""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_securetransfer_privacy.hooks import apply_french

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    written = apply_french(env)
    _logger.info("bf_securetransfer_privacy: %d register text(s) put in French", len(written))
