import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Grant the subscription manager group to Settings administrators.

    `base.group_system` is flagged noupdate, so the declarative implied_ids
    link only applies on a fresh install; on upgrade we apply it explicitly so
    existing admins can manage report/digest configs.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    group_system = env.ref('base.group_system', raise_if_not_found=False)
    group_mgr = env.ref('bf_subscription.group_subscription_manager', raise_if_not_found=False)
    if not group_system or not group_mgr:
        _logger.warning("bf_subscription migration: groups not found, skipping")
        return
    if group_mgr not in group_system.implied_ids:
        group_system.write({'implied_ids': [(4, group_mgr.id)]})
        _logger.info(
            "bf_subscription: granted subscription manager group to %d admins",
            len(group_mgr.users),
        )
