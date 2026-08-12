"""Post-migration to 18.0.2.4.0.

Re-runs the IMAP archive writeback for handled rows whose IMAP-side
move never happened. Two cohorts get caught up:

1. Rows with no ``imap_uid`` because the chatter cron created the
   ``bf.email`` row before the IMAP cron saw the UID. Until 2.4.0,
   ``_ingest_rfc822`` only backfilled UIDs on rows whose source was
   already ``imap``, so gateway/chatter rows stayed UID-less and the
   writeback filtered them out. The new writeback uses a
   ``HEADER Message-ID`` SEARCH fallback that finds them.

2. Rows with a stale ``imap_uid`` whose ``imap_folder`` is still
   ``INBOX`` despite the message having been archived elsewhere — same
   HEADER lookup catches up.

Bounded to the last 180 days to keep the IMAP load reasonable. The
``_cron_imap_mirror`` will continue reconciling ``imap_in_inbox``
afterwards on its 5-minute cadence.
"""

import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    cutoff = fields.Datetime.now() - timedelta(days=180)
    targets = env["bf.email"].search([
        ("is_handled", "=", True),
        ("imap_in_inbox", "=", True),
        ("message_id_header", "!=", False),
        ("date", ">=", cutoff),
    ])
    if not targets:
        _logger.info(
            "bf_email_management 18.0.2.4.0: no handled-but-still-in-inbox rows"
        )
        return

    _logger.info(
        "bf_email_management 18.0.2.4.0: replaying IMAP writeback "
        "for %s handled rows still flagged as in-inbox",
        len(targets),
    )
    # Chunked to keep the IMAP transaction tidy and to avoid a single
    # giant connection hogging the worker for minutes.
    BATCH = 50
    for offset in range(0, len(targets), BATCH):
        chunk = targets[offset:offset + BATCH]
        try:
            chunk._imap_writeback_archive()
        except Exception:
            _logger.warning(
                "bf_email_management 18.0.2.4.0: writeback chunk failed "
                "(offset %s, size %s) — continuing",
                offset, len(chunk), exc_info=True,
            )
            continue
