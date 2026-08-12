# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Migration 18.0.2.0.0: multi-backend support (Nextcloud + Google).

Defensive: ensures every existing config has backend_type='nextcloud'
before the NOT NULL constraint is created by the ORM on upgrade.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'nextcloud_calendar_sync_config'
          AND column_name = 'backend_type'
    """)
    if not cr.fetchone():
        _logger.info(
            "Adding backend_type column with default 'nextcloud' "
            "on nextcloud_calendar_sync_config"
        )
        cr.execute("""
            ALTER TABLE nextcloud_calendar_sync_config
            ADD COLUMN backend_type varchar DEFAULT 'nextcloud'
        """)
        cr.execute("""
            UPDATE nextcloud_calendar_sync_config
            SET backend_type = 'nextcloud'
            WHERE backend_type IS NULL
        """)
        count = cr.rowcount
        if count:
            _logger.info(
                "Backfilled backend_type='nextcloud' for %d existing configs",
                count,
            )
