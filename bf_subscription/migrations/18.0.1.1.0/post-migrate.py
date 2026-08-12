import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Grant the subscription user group to all internal users.

    The declarative `base.group_user` implied_ids link only applies on a fresh
    install (init mode); on upgrade it is skipped because `base.group_user` is
    flagged noupdate. This migration applies it explicitly so the app menu
    becomes visible to existing internal users.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    group_user = env.ref('base.group_user', raise_if_not_found=False)
    group_sub = env.ref('bf_subscription.group_subscription_user', raise_if_not_found=False)
    if not group_user or not group_sub:
        _logger.warning("bf_subscription migration: groups not found, skipping")
        return
    if group_sub not in group_user.implied_ids:
        group_user.write({'implied_ids': [(4, group_sub.id)]})
        _logger.info(
            "bf_subscription: granted subscription user group to %d internal users",
            len(group_sub.users),
        )
