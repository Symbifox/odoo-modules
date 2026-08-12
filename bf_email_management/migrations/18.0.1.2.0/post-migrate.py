import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Backfill bf.email.source from the linked mail.message.message_type.

    Existing records had no source field; derive it from the source message
    so filters work immediately after upgrade. Null-safe: rows with no
    mail_message_id keep source=NULL.
    """
    cr.execute(
        """
        UPDATE bf_email be
        SET source = CASE
            WHEN mm.message_type = 'email' THEN 'gateway'
            ELSE 'chatter'
        END
        FROM mail_message mm
        WHERE be.mail_message_id = mm.id
          AND be.source IS NULL
        """
    )
    _logger.info(
        "bf_email: backfilled source on %d rows", cr.rowcount
    )
