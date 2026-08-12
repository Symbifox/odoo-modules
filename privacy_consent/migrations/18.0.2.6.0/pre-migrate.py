"""Pre-migration: Allow re-loading of notice and purpose data.

Flip noupdate=False for all privacy.notice and privacy.purpose records
in ir_model_data so the module upgrade re-reads the XML data files and
updates DB records to match the authoritative consent form texts.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info(
        "privacy_consent pre-migrate 2.6.0: "
        "flipping noupdate for notice and purpose records"
    )

    cr.execute(
        """
        UPDATE ir_model_data
        SET noupdate = FALSE
        WHERE module = 'privacy_consent'
          AND model IN ('privacy.notice', 'privacy.purpose')
          AND noupdate = TRUE
        """
    )
    _logger.info(
        "  Flipped noupdate for %d records",
        cr.rowcount,
    )
