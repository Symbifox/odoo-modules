def post_init_hook(env):
    """Stamp the presences noted before the installation (see meeting_attendance.py).

    Set ``bf_work_hours_meeting.processing_logins`` BEFORE installing: the
    presences the processing wrote are judged with it.
    """
    env["meeting.attendance"]._bf_work_stamp_existing()
