from . import models


def post_init_hook(env):
    """Days already on the road when the module arrives: their start was received live."""
    env.cr.execute("UPDATE bf_route_day SET start_received_at = date_started "
                   "WHERE state = 'in_progress' AND start_received_at IS NULL")
