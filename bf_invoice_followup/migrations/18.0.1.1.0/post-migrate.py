import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Switch the digest to per-user opt-in. Preserve the prior single-recipient
    behavior by subscribing the previously-configured digest user (failing
    that, uid 2 — the first administrator) at the 14-day cadence, so the
    recap keeps delivering."""
    cr.execute(
        "SELECT value FROM ir_config_parameter "
        "WHERE key = 'bf_invoice_followup.digest_user_id'"
    )
    row = cr.fetchone()
    try:
        uid = int(row[0]) if row and row[0] else 2
    except (TypeError, ValueError):
        uid = 2
    cr.execute(
        "UPDATE res_users "
        "SET bf_followup_digest = TRUE, bf_followup_digest_interval = '14' "
        "WHERE id = %s",
        (uid,),
    )
    _logger.info(
        "bf_invoice_followup: subscribed user #%s to the per-user digest (14 j)", uid)
