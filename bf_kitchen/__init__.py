from . import models
from . import report
from . import wizard


def post_init_hook(env):
    """Kitchen locations under every warehouse, and the kitchen's loss reasons."""
    env["stock.warehouse"]._bf_kitchen_create_all_locations()
    env["stock.scrap.reason.tag"]._bf_kitchen_create_reasons()
