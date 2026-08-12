"""Post-migration to 18.0.2.0.0 — Inbox-Zero refresh.

Six things, in order:

1. Backfill ``is_handled`` from prior archive semantics
   (status='archived' OR active=False → is_handled=True).
2. Infer pre-archive status (read vs replied) from sibling outbound rows
   so the user keeps history once we stop overloading status='archived'.
3. Reset active=True on rows that were soft-deleted by the old archive
   (active is no longer the master archive switch).
4. Backfill imap_in_inbox: True when imap_folder is INBOX or empty (live
   IMAP mirror cron will re-reconcile on next tick; chatter rows always
   show by default), False for folders matching Archives/*.
5. Trigger _compute_signals over all rows so heuristic booleans land
   immediately (not lazy on next access).
6. Create the pg_trgm GIN index on body_preview for fast search.
"""

import logging

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # ------------------------------------------------------------------
    # Step 1 + 3: backfill is_handled, reset active
    # ------------------------------------------------------------------
    cr.execute(
        """
        UPDATE bf_email
           SET is_handled = TRUE,
               handled_at = COALESCE(write_date, NOW())
         WHERE (status = 'archived' OR active = FALSE)
           AND is_handled = FALSE
        """
    )
    handled_backfill = cr.rowcount
    _logger.info(
        "bf_email_management 18.0.2.0.0: %s rows flagged is_handled=True",
        handled_backfill,
    )

    cr.execute(
        """
        UPDATE bf_email
           SET active = TRUE
         WHERE active = FALSE
        """
    )
    reactivated = cr.rowcount
    _logger.info(
        "bf_email_management 18.0.2.0.0: %s rows reactivated", reactivated,
    )

    # ------------------------------------------------------------------
    # Step 2: infer pre-archive status — replied if any later outbound
    # row exists with the same thread_root_id, else read.
    # ------------------------------------------------------------------
    cr.execute(
        """
        WITH replied_threads AS (
            SELECT DISTINCT thread_root_id
              FROM bf_email
             WHERE direction = 'out'
               AND thread_root_id IS NOT NULL
        )
        UPDATE bf_email be
           SET status = CASE
                          WHEN be.thread_root_id IN (SELECT thread_root_id FROM replied_threads)
                            THEN 'replied'
                          ELSE 'read'
                        END
         WHERE be.status = 'archived'
        """
    )
    inferred = cr.rowcount
    _logger.info(
        "bf_email_management 18.0.2.0.0: %s archived rows reassigned to read/replied",
        inferred,
    )

    # ------------------------------------------------------------------
    # Step 4: imap_in_inbox backfill
    # ------------------------------------------------------------------
    cr.execute(
        """
        UPDATE bf_email
           SET imap_in_inbox = (
               imap_folder IS NULL
               OR imap_folder = ''
               OR UPPER(imap_folder) = 'INBOX'
           )
        """
    )
    _logger.info(
        "bf_email_management 18.0.2.0.0: imap_in_inbox initialized for %s rows",
        cr.rowcount,
    )

    # ------------------------------------------------------------------
    # Step 5: trigger heuristic signal compute
    # ------------------------------------------------------------------
    env = api.Environment(cr, SUPERUSER_ID, {})
    rows = env["bf.email"].search([])
    if rows:
        rows.invalidate_recordset([
            "is_short", "is_question", "is_action_request", "is_to_me",
            "is_late_night", "is_likely_thread", "is_bulk",
        ])
        try:
            rows._compute_signals()
            rows.flush_recordset([
                "is_short", "is_question", "is_action_request", "is_to_me",
                "is_late_night", "is_likely_thread", "is_bulk",
            ])
            _logger.info(
                "bf_email_management 18.0.2.0.0: heuristic signals computed for %s rows",
                len(rows),
            )
        except Exception:
            _logger.warning(
                "bf_email_management 18.0.2.0.0: signal compute failed",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Step 6: pg_trgm GIN index on body_preview for fast search
    # ------------------------------------------------------------------
    try:
        cr.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cr.execute("""
            CREATE INDEX IF NOT EXISTS bf_email_body_preview_trgm_idx
                ON bf_email USING gin (body_preview gin_trgm_ops)
        """)
        _logger.info(
            "bf_email_management 18.0.2.0.0: pg_trgm index on body_preview created",
        )
    except Exception:
        _logger.warning(
            "bf_email_management 18.0.2.0.0: pg_trgm index creation skipped",
            exc_info=True,
        )

    # ------------------------------------------------------------------
    # Final: kick off expected_reply_minutes recompute (one-shot)
    # ------------------------------------------------------------------
    try:
        env["bf.email"]._cron_recompute_expected_reply()
    except Exception:
        _logger.warning(
            "bf_email_management 18.0.2.0.0: expected_reply_minutes initial compute skipped",
            exc_info=True,
        )
