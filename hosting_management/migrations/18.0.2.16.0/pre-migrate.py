# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Pre-migration: Drop the hosting_dashboard transient table.

    The model changed from TransientModel (with _auto=True, creating a DB table)
    to Model with _auto=False (no DB table — pure API model for the OWL dashboard).

    Also clean up the old ir.ui.view (form view) and ir.actions.act_window record
    that are being replaced by ir.actions.client.
    """
    if not version:
        return

    _logger.info(
        "Pre-migration 18.0.2.16.0: "
        "Converting hosting.dashboard from transient to _auto=False"
    )

    # Drop the old transient table if it exists
    cr.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_name = 'hosting_dashboard'
        )
    """)
    if cr.fetchone()[0]:
        cr.execute("DROP TABLE hosting_dashboard CASCADE")
        _logger.info("Dropped hosting_dashboard transient table")

    # Remove the old form view record (ir.ui.view)
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE model = 'hosting.dashboard'
    """)
    _logger.info("Removed old hosting.dashboard form views")

    # Remove old act_window action and replace with client action
    # The XML ID hosting_management.hosting_dashboard_action was ir.actions.act_window
    # and is now ir.actions.client — we need to clean up the old record
    cr.execute("""
        DELETE FROM ir_act_window
        WHERE res_model = 'hosting.dashboard'
    """)
    _logger.info("Removed old hosting.dashboard act_window actions")

    # Remove the ir.model.data entry so the new client action can be created
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'hosting_management'
          AND name = 'hosting_dashboard_action'
    """)
    _logger.info("Removed old ir.model.data for hosting_dashboard_action")

    _logger.info("Pre-migration 18.0.2.16.0 completed")
