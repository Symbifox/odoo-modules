"""Restore color override view (v18.0.1.13.0).

NOTE: Originally inserted a calendar_filters row for the current user,
but Odoo 18 JS auto-creates a runtime filter for the logged-in user's
partner. A DB row with the same partner_id causes a duplicate key error
in OWL's t-foreach in CalendarFilterPanel. The insert was removed.
"""


def migrate(cr, version):
    pass
