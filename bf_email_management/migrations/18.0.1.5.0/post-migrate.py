"""Post-migration to 18.0.1.5.0 — IMAP direct ingestion + reroute wizard.

Backfills ``thread_root_id`` for existing rows by walking the parent chain
of their linked ``mail.message``. IMAP backfill itself is opt-in via the
new wizard (volume too large to do automatically).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("bf_email_management 18.0.1.5.0 post-migrate: backfilling thread_root_id")

    # Use parent_id chain: thread_root = topmost parent message_id, fallback self.
    cr.execute(
        """
        WITH RECURSIVE chain(id, parent_id, message_id, root_message_id) AS (
            SELECT id, parent_id, message_id, message_id
              FROM mail_message
             WHERE parent_id IS NULL

            UNION ALL

            SELECT m.id, m.parent_id, m.message_id, c.root_message_id
              FROM mail_message m
              JOIN chain c ON m.parent_id = c.id
        )
        UPDATE bf_email be
           SET thread_root_id = COALESCE(chain.root_message_id, be.message_id_header)
          FROM chain
         WHERE be.mail_message_id = chain.id
           AND be.thread_root_id IS DISTINCT FROM COALESCE(chain.root_message_id, be.message_id_header)
        """
    )
    _logger.info("bf_email_management 18.0.1.5.0: thread_root_id updated for %s rows", cr.rowcount)

    # Rows without a linked mail.message: thread_root_id = in_reply_to or message_id_header
    cr.execute(
        """
        UPDATE bf_email
           SET thread_root_id = COALESCE(in_reply_to, message_id_header)
         WHERE thread_root_id IS NULL
           AND COALESCE(in_reply_to, message_id_header) IS NOT NULL
        """
    )
    _logger.info("bf_email_management 18.0.1.5.0: thread_root_id seeded on %s orphan rows", cr.rowcount)
