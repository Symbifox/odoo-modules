from . import models
from . import wizard


def _post_init_backfill(env):
    """Backfill XP for the last 96 hours of work on fresh install."""
    env['bf.gamification.profile']._backfill_recent_xp(hours=96)
