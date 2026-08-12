"""Post-migration to 18.0.3.3.0.

Replays the IMAP archive writeback for handled rows whose IMAP-side
move never happened because the rule engine auto-handled them.

Until 3.3.0 ``_apply_rules`` wrote ``is_handled=True`` directly via
``rec.write(vals)`` and never triggered ``_imap_writeback_archive``.
Rules like "List-Unsubscribe → Marketing + Traité" and
"Expéditeurs noreply → Notification + Traité" therefore left every
matching message in the IMAP INBOX while marking it Traité in Odoo.

The 18.0.2.4.0 backfill caught the gateway/chatter race-condition
cohort but not this one — it was never the same root cause.

Bounded to the last 180 days. The ``_cron_imap_mirror`` will continue
reconciling ``imap_in_inbox`` afterwards on its 5-minute cadence.
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
            "bf_email_management 18.0.3.3.0: no handled-but-still-in-inbox rows"
        )
        return

    _logger.info(
        "bf_email_management 18.0.3.3.0: replaying IMAP writeback "
        "for %s handled rows still flagged as in-inbox",
        len(targets),
    )
    BATCH = 50
    for offset in range(0, len(targets), BATCH):
        chunk = targets[offset:offset + BATCH]
        try:
            chunk._imap_writeback_archive()
        except Exception:
            _logger.warning(
                "bf_email_management 18.0.3.3.0: writeback chunk failed "
                "(offset %s, size %s) — continuing",
                offset, len(chunk), exc_info=True,
            )
            continue
