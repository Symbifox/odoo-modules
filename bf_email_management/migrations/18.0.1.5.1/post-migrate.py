"""Post-migration to 18.0.1.5.1.

Two things:
1. ``body_html`` is now a stored compute (was related). Backfill values
   from mail.message.body for chatter rows; parse raw_rfc822 for IMAP rows.
2. Promote IMAP orphans whose Message-ID already exists in mail.message
   (= "internal Odoo wins" rule). The 1.5.0 IMAP cron created orphans
   without checking mail.message; this sweep links them retroactively.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # ------------------------------------------------------------------
    # Step 1: promote IMAP orphans that have a mail.message twin.
    # Internal Odoo wins — link mail_message_id, copy res_model/res_id,
    # switch source from 'imap' to 'gateway'/'chatter'.
    # ------------------------------------------------------------------
    cr.execute(
        """
        WITH twins AS (
            SELECT be.id AS bf_id,
                   mm.id AS mm_id,
                   mm.model AS res_model,
                   mm.res_id AS res_id,
                   CASE WHEN mm.message_type = 'email' THEN 'gateway'
                        ELSE 'chatter' END AS new_source
              FROM bf_email be
              JOIN mail_message mm ON mm.message_id = be.message_id_header
             WHERE be.source = 'imap'
               AND be.mail_message_id IS NULL
        )
        UPDATE bf_email be
           SET mail_message_id = twins.mm_id,
               res_model = twins.res_model,
               res_id = twins.res_id,
               source = twins.new_source
          FROM twins
         WHERE be.id = twins.bf_id
        """
    )
    promoted = cr.rowcount
    _logger.info(
        "bf_email_management 18.0.1.5.1: promoted %s IMAP orphans to chatter/gateway twins",
        promoted,
    )

    # ------------------------------------------------------------------
    # Step 2: backfill body_html from mail.message (fast SQL path).
    # ------------------------------------------------------------------
    cr.execute(
        """
        UPDATE bf_email be
           SET body_html = mm.body
          FROM mail_message mm
         WHERE be.mail_message_id = mm.id
           AND mm.body IS NOT NULL
           AND mm.body <> ''
        """
    )
    _logger.info(
        "bf_email_management 18.0.1.5.1: body_html backfilled from mail.message for %s rows",
        cr.rowcount,
    )

    # ------------------------------------------------------------------
    # Step 3: backfill record_name for promoted rows.
    # ------------------------------------------------------------------
    env = api.Environment(cr, SUPERUSER_ID, {})
    promoted_rows = env["bf.email"].search([
        ("mail_message_id", "!=", False),
        ("res_model", "!=", False),
        ("res_id", "!=", 0),
        ("record_name", "in", [False, ""]),
    ])
    for row in promoted_rows:
        try:
            target = env[row.res_model].sudo().browse(row.res_id)
            if target.exists():
                row.record_name = (target.display_name or "")[:200]
        except Exception:
            continue

    # ------------------------------------------------------------------
    # Step 4: parse raw_rfc822 for remaining IMAP orphans (no twin).
    # ------------------------------------------------------------------
    orphans = env["bf.email"].search([
        ("source", "=", "imap"),
        ("mail_message_id", "=", False),
    ])
    if orphans:
        _logger.info(
            "bf_email_management 18.0.1.5.1: parsing raw_rfc822 for %s IMAP-orphan rows",
            len(orphans),
        )
        orphans.invalidate_recordset(["body_html"])
        orphans._compute_body_html()
        orphans.flush_recordset(["body_html"])
