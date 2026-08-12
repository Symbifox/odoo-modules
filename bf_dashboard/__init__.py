from . import models


def _set_home_action(env):
    """Set the unified dashboard as home action for all internal users."""
    action = env.ref("bf_dashboard.bf_dashboard_action")
    internal_users = env["res.users"].search([("share", "=", False)])
    internal_users.write({"action_id": action.id})
