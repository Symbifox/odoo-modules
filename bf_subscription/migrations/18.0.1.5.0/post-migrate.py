import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Re-point the renewal alerts already standing onto their dedicated type.

    Until 1.5.0 the cron raised them as plain To-Do. The confirm button closes
    activities by type, so an alert left on the old type would survive the
    decision and keep sitting on the record.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    act_type = env.ref('bf_subscription.mail_activity_type_renewal', raise_if_not_found=False)
    if not act_type:
        _logger.warning("bf_subscription 1.5.0: renewal activity type missing, skipping")
        return
    domain = [
        ('res_model', '=', 'subscription.subscription'),
        ('summary', '=like', 'Renouvellement à venir%'),
        ('activity_type_id', '!=', act_type.id),
    ]
    legacy = env['mail.activity'].search(domain)
    if legacy:
        legacy.write({'activity_type_id': act_type.id})
    _logger.info("bf_subscription 1.5.0: %d renewal activities re-typed", len(legacy))
