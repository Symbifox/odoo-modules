"""Remove the calendar color override view that broke sidebar attendee filter.

The XML override changed color="partner_ids" to color="color" on the main
calendar view. This broke the sidebar filter mechanism because the color
attribute does double duty: it sets both the coloring field AND creates the
sidebar filter section. The JS patch (attendee_calendar_color_patch.js)
handles coloring independently without breaking the filter.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Remove the ir.ui.view record and its ir_model_data entry
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'calendar_nextcloud_sync'
          AND name = 'view_calendar_event_calendar_color_override'
    """)
    deleted = cr.rowcount
    _logger.info("Deleted %d ir_model_data entries for color override view", deleted)

    cr.execute("""
        DELETE FROM ir_ui_view
        WHERE name = 'calendar.event.calendar.color.override'
          AND model = 'calendar.event'
    """)
    deleted = cr.rowcount
    _logger.info("Deleted %d ir_ui_view records for color override view", deleted)
