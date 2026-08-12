# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Pre-migration: Clean up the old hosting.dashboard transient model artifacts.

    The model changed from TransientModel (with DB table and form view)
    to Model with _auto=False (OWL client action, no DB table).

    This migration handles cleanup that the 18.0.2.16.0 migration missed
    (version was already bumped before the script was added).
    """
    if not version:
        return

    _logger.info(
        "Pre-migration 18.0.2.16.1: "
        "Cleaning up old hosting.dashboard transient model"
    )

    # 1. Drop the old transient table if it still exists
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'hosting_dashboard'
        )
    """)
    if cr.fetchone()[0]:
        cr.execute("DROP TABLE hosting_dashboard CASCADE")
        _logger.info("Dropped hosting_dashboard transient table")

    # 2. Remove old form view records
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE model = 'hosting.dashboard'
    """)
    _logger.info("Removed old hosting.dashboard views")

    # 3. Remove old act_window action
    cr.execute("""
        DELETE FROM ir_act_window
        WHERE res_model = 'hosting.dashboard'
    """)
    _logger.info("Removed old hosting.dashboard act_window actions")

    # 4. Remove stale ir_model_data entries so the new client action
    #    and updated menu can be created cleanly
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'hosting_management'
          AND name IN (
              'hosting_dashboard_action',
              'hosting_dashboard_view_form'
          )
    """)
    _logger.info("Removed stale ir_model_data entries")

    _logger.info("Pre-migration 18.0.2.16.1 completed")
