# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Add report_type column with default 'legacy' BEFORE Odoo's ORM tries.

    The new column is declared with `default='legacy'` in the model, but
    Odoo 18 fills the default only on rows created *after* the column
    appears. By creating the column ourselves with a default in pre-migrate,
    we backfill all existing rows in a single ALTER TABLE — much faster on
    large tables, and avoids the ORM trying to UPDATE row-by-row.
    """
    if not version:
        return

    cr.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name='hosting_backup_run' AND column_name='report_type'
        """
    )
    if cr.fetchone():
        _logger.info("report_type column already exists; nothing to backfill")
        return

    cr.execute(
        """
        ALTER TABLE hosting_backup_run
        ADD COLUMN report_type VARCHAR DEFAULT 'legacy' NOT NULL
        """
    )
    _logger.info(
        "Added report_type column with default 'legacy' "
        "(backfilled all existing runs in one shot)"
    )
