"""freeze the elapsed time of timers ALREADY stopped at upgrade.

From 1.12.0 on, a stopped timer carries its elapsed time in
``accumulated_seconds`` and nothing recomputes it from ``start_time``. Timers
stopped by 1.11.x never had their last segment folded in, so without this step
they would suddenly propose too little (only what earlier pauses accumulated).

The stop instant is ``claimed_at``: both stop paths of 1.11.x stamp it in the
same write as ``is_active = False``. ``write_date`` is the fallback.

🔴 **Played once, and only once.** Odoo commits a post-migration BEFORE it
writes ``latest_version``: a ``-u`` that fails further down the cascade leaves
the module at 1.11.x, and the next ``-u`` plays this script again, which would
add the same segment a second time. The marker
``bf_timesheet_timer.migration_1_12_0_fige`` is read first and written in the
same transaction as the freeze.

⚠️ Known limit, logged per timer: 1.11.x reset ``is_paused`` at stop, so a
timer that was PAUSED when stopped cannot be told apart. Its pause is counted
as worked time, exactly the value 1.11.x itself proposed from the stop instant.
There were 0 stopped timers on the five tenants on 2026-09-13.

⚠️ **Stop the Odoo container during the upgrade.** A ``-u`` played with
``docker exec`` inside the LIVE container runs in a second process; the live
one keeps its 1.11.x Python and keeps stopping timers the old way (no fold)
while and after this script runs. Those timers are then read by 1.12.0 as
frozen, without their last segment. Upgrade with the service stopped, then
start it.
"""
import logging

_logger = logging.getLogger(__name__)

MARKER = "bf_timesheet_timer.migration_1_12_0_fige"


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (MARKER,))
    deja = cr.fetchone()
    if deja:
        _logger.info("bf_timesheet_timer 1.12.0: stopped timers already frozen (%s), skipped",
                     deja[0])
        return
    cr.execute("""
        UPDATE bf_timer
           SET accumulated_seconds = GREATEST(
                   0,
                   COALESCE(accumulated_seconds, 0)
                   + EXTRACT(EPOCH FROM (COALESCE(claimed_at, write_date) - start_time)))
         WHERE is_active = FALSE
     RETURNING id, user_id, accumulated_seconds
    """)
    rows = cr.fetchall()
    for timer_id, user_id, seconds in rows:
        _logger.warning(
            "bf_timesheet_timer 1.12.0: stopped timer %s (user %s) frozen at %.0f s",
            timer_id, user_id, seconds)
    cr.execute("""
        INSERT INTO ir_config_parameter (key, value, create_uid, write_uid, create_date, write_date)
        VALUES (%s, %s, 1, 1, NOW() AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')
        ON CONFLICT (key) DO NOTHING
    """, (MARKER, "%d timer(s)" % len(rows)))
    _logger.info("bf_timesheet_timer 1.12.0: %d stopped timer(s) frozen", len(rows))
