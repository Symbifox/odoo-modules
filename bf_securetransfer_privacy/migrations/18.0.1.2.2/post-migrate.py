"""18.0.1.2.2: the retention code comes back to the one the fleet carries, the
delivered register texts take the current French, and the purpose summary's
source term is put back in English. See hooks.py."""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.bf_securetransfer_privacy.hooks import (
    apply_french,
    repair_code,
    restore_sources,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    if repair_code(env):
        _logger.info("bf_securetransfer_privacy: the retention rule carries the "
                     "delivered code again")
    written = apply_french(env)
    _logger.info("bf_securetransfer_privacy: %d register text(s) put in French: %s",
                 len(written), ", ".join(written) or "none")
    restored = restore_sources(env)
    _logger.info("bf_securetransfer_privacy: %d source term(s) put back in English: %s",
                 len(restored), ", ".join(restored) or "none")
