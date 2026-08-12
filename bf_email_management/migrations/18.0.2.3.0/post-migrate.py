"""Post-migration to 18.0.2.3.0 — bilateral IMAP archive on by default.

Flip existing ``bf_email.imap_writeback_archive`` from "False" to "True"
unless an admin has explicitly set it to something else (e.g. left "False"
deliberately, set to "0", etc.). We only flip the canonical "False" so
deliberate opt-outs survive the upgrade — admins can re-disable any time.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_config_parameter
           SET value = 'True'
         WHERE key = 'bf_email.imap_writeback_archive'
           AND value = 'False'
        """,
    )
    if cr.rowcount:
        _logger.info(
            "bf_email_management 18.0.2.3.0: flipped "
            "bf_email.imap_writeback_archive False → True (%s row)",
            cr.rowcount,
        )
