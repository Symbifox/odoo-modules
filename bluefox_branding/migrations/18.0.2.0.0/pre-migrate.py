"""Ownership transfer: report_brand_primary / report_brand_dark move from bf_lexend.

Before 18.0.2.0.0 these two res.company columns were declared by bf_lexend. Starting
this version, bluefox_branding owns them. The columns themselves were already created
by bf_lexend, so there is nothing to migrate — this pre-migrate just guards against
the unlikely case where bluefox_branding is being installed against a tenant that
never had bf_lexend (the dependency declared in __manifest__.py should prevent that,
but the check is cheap).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'res_company'
          AND column_name IN ('report_brand_primary', 'report_brand_dark')
        """
    )
    existing = {row[0] for row in cr.fetchall()}
    if existing != {"report_brand_primary", "report_brand_dark"}:
        _logger.info(
            "bluefox_branding 18.0.2.0.0: brand color columns not yet present "
            "(found %s) — Odoo will create them from the new field declarations.",
            sorted(existing),
        )
    else:
        _logger.info(
            "bluefox_branding 18.0.2.0.0: brand color columns already present, "
            "ownership transferred from bf_lexend with data preserved."
        )
