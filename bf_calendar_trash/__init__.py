from . import models
from . import wizard


def _bf_trash_existing_archives(env):
    """Every meeting already archived enters the trash, as it is.

    By design: nothing is purged, and what was archived
    before the trash existed is what people meant by "deleted" (core's calendar
    has archived on its "Delete" button since 17.0). Dated by its last write,
    attributed to its last writer, each in a batch of its own: restoring one
    must not bring back the 718 other dates of a series archived the same day.
    """
    env.cr.execute("""
        UPDATE calendar_event
           SET bf_trashed_at = COALESCE(write_date, create_date),
               bf_trashed_by = write_uid,
               bf_trash_batch = 'before-' || id,
               bf_trash_origin = 'odoo'
         WHERE active IS NOT TRUE
           AND bf_trashed_at IS NULL
    """)
