def post_init_hook(env):
    """Stamp the calendar answers given before the installation (see calendar_attendee.py)."""
    env["calendar.attendee"]._bf_work_stamp_existing()
