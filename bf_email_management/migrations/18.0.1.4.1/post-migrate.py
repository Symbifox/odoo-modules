"""Backfill bf.email rows for mail.message records the cron missed.

The previous cron filtered on ``mail.message.date`` (sender's send time)
instead of ``create_date`` (insertion time). Any backdated insert — manual
IMAP imports, forwarded emails carrying the original ``Date:`` header —
fell below the moving watermark and was never projected into bf.email.

This migration sweeps every email-type message that lacks a bf.email row
and runs the regular sync logic on it. Dedup is enforced by the existing
UNIQUE (message_id_header, company_id) constraint via _should_sync, so
re-runs are safe.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    BfEmail = env["bf.email"].sudo()
    Message = env["mail.message"].sudo()

    cr.execute(
        """
        SELECT m.id
        FROM mail_message m
        LEFT JOIN bf_email b ON b.mail_message_id = m.id
        WHERE b.id IS NULL
          AND (
              m.message_type = 'email'
              OR (
                  m.message_type = 'comment'
                  AND EXISTS (
                      SELECT 1
                      FROM mail_notification n
                      WHERE n.mail_message_id = m.id
                        AND n.notification_type = 'email'
                  )
              )
          )
        ORDER BY m.create_date ASC
        """
    )
    msg_ids = [row[0] for row in cr.fetchall()]

    if not msg_ids:
        _logger.info("bf.email backfill: nothing to do")
        return

    _logger.info("bf.email backfill: %d candidate messages", len(msg_ids))

    created = 0
    skipped = 0
    for chunk_start in range(0, len(msg_ids), 500):
        chunk = msg_ids[chunk_start:chunk_start + 500]
        for msg in Message.browse(chunk):
            if not BfEmail._should_sync(msg):
                skipped += 1
                continue
            vals = BfEmail._prepare_email_vals(msg)
            if not vals:
                skipped += 1
                continue
            try:
                with cr.savepoint():
                    BfEmail.with_context(
                        mail_create_nosubscribe=True,
                        tracking_disable=True,
                    ).create(vals)
                created += 1
            except Exception:
                _logger.warning(
                    "bf.email backfill: failed for mail.message %s",
                    msg.id,
                    exc_info=True,
                )
                skipped += 1
        env.cr.commit()

    _logger.info(
        "bf.email backfill done: %d created, %d skipped (of %d)",
        created,
        skipped,
        len(msg_ids),
    )

    # Realign watermark on max(create_date) we just absorbed so the cron
    # picks up cleanly from the next genuinely new row.
    cr.execute("SELECT MAX(create_date) FROM mail_message")
    latest = cr.fetchone()[0]
    if latest:
        ICP = env["ir.config_parameter"].sudo()
        ICP.set_param("bf_email.last_sync_date", str(latest))
        _logger.info("bf.email watermark advanced to %s", latest)
