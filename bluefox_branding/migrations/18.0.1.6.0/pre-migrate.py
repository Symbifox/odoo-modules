"""Remove old blanket layout override before loading new standalone layouts.

The v18.0.1.5.1 version created an inherit override of
mail.mail_notification_layout which branded ALL emails including chatter.
This migration removes it so only the new standalone BF layouts are used.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Delete the old inherit view from ir_ui_view
    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE key = 'bluefox_branding.bf_override_notification_layout'
    """)
    deleted = cr.rowcount
    if deleted:
        _logger.info("bluefox_branding: Removed old inherit override bf_override_notification_layout")

    # Clean up ir_model_data reference
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'bluefox_branding'
        AND name = 'bf_override_notification_layout'
    """)
