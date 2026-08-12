# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Rename webhook_secret column to webhook_secret_encrypted.

    The field is now a computed/inverse field with encrypted storage.
    Old unencrypted values will be transparently handled by _decrypt_value
    (Fernet InvalidToken fallback returns value as-is).
    """
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'nextcloud_calendar_sync_config'
          AND column_name = 'webhook_secret'
    """)
    if cr.fetchone():
        _logger.info(
            "Renaming webhook_secret → webhook_secret_encrypted "
            "on nextcloud_calendar_sync_config"
        )
        cr.execute("""
            ALTER TABLE nextcloud_calendar_sync_config
            RENAME COLUMN webhook_secret TO webhook_secret_encrypted
        """)
