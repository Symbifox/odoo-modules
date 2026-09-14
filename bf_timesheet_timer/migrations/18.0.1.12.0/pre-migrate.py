"""``first_start`` for the timers that exist at upgrade.

1.12.0 dates a timesheet on the owner's day at the timer's FIRST start
(``bf.timer._timesheet_date``). Timers created by 1.11.x have no such value;
their ``create_date`` is the closest thing: ``start_timer`` creates the record
at the moment it starts, and ``create_date`` never moves afterwards, unlike
``start_time``.

⚠️ PRE-migrate, adding the column itself: left to the ORM, the new column would
be filled with the field default, i.e. the moment of the upgrade, and every
running timer would be dated on the upgrade day.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE bf_timer ADD COLUMN IF NOT EXISTS first_start timestamp")
    cr.execute("""
        UPDATE bf_timer
           SET first_start = COALESCE(create_date, start_time)
         WHERE first_start IS NULL
    """)
    _logger.info("bf_timesheet_timer 1.12.0: first_start set on %d existing timer(s)",
                 cr.rowcount)
