"""Post-migration: Update portal IR rule to include involved parties."""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Update rule_consent_portal to also grant access to involved partners.

    The rule is in noupdate="1", so it must be patched via SQL.

    Before: [('subject_partner_id', '=', user.partner_id.id)]
    After:  ['|', ('subject_partner_id', '=', user.partner_id.id),
                   ('involved_partner_ids', 'in', [user.partner_id.id])]
    """
    _logger.info("Updating portal consent IR rule to include involved_partner_ids")

    cr.execute("""
        UPDATE ir_rule
        SET domain_force = $$['|', ('subject_partner_id', '=', user.partner_id.id), ('involved_partner_ids', 'in', [user.partner_id.id])]$$
        WHERE id = (
            SELECT res_id FROM ir_model_data
            WHERE module = 'privacy_consent' AND name = 'rule_consent_portal'
        )
    """)

    if cr.rowcount:
        _logger.info("Updated rule_consent_portal domain successfully")
    else:
        _logger.warning("rule_consent_portal not found — skipping")
