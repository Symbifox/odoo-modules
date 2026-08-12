"""Pre-migration for 18.0.3.1.0.

Drops legacy certificate_file / certificate_filename columns on
privacy.destruction.request — these were replaced by the QWeb report action
in v3.0.1 (fix #4) but the field declarations remained.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Detach ir.attachment rows that backed certificate_file to release storage.
    cr.execute(
        """
        DELETE FROM ir_attachment
        WHERE res_model = 'privacy.destruction.request'
          AND res_field IN ('certificate_file', 'certificate_filename')
        """
    )
    _logger.info("Removed %s legacy certificate_file attachments", cr.rowcount)

    for col in ("certificate_file", "certificate_filename"):
        cr.execute(
            """
            ALTER TABLE privacy_destruction_request
            DROP COLUMN IF EXISTS %s
            """ % col
        )
