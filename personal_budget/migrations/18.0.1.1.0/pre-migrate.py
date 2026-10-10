"""Replace old French seed categories/contributors with new English ones."""
import logging
from odoo.tools.sql import column_exists

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    _logger.info("personal_budget: clearing old seed data for re-creation")

    # Check if any transactions reference categories
    cr.execute("""
        SELECT COUNT(*) FROM personal_budget_transaction
        WHERE category_id IS NOT NULL
    """)
    txn_count = cr.fetchone()[0]
    if txn_count > 0:
        _logger.warning(
            "Found %d transactions with categories — skipping category wipe",
            txn_count,
        )
        return

    # Delete old ir_model_data entries for categories and contributors
    # so the XML seed data will re-insert them fresh
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'personal_budget'
          AND model = 'personal.budget.category'
    """)
    deleted = cr.rowcount
    _logger.info("Deleted %d ir_model_data entries for categories", deleted)

    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'personal_budget'
          AND model = 'personal.budget.contributor'
    """)
    deleted = cr.rowcount
    _logger.info("Deleted %d ir_model_data entries for contributors", deleted)

    # Delete the actual records
    cr.execute("DELETE FROM personal_budget_category")
    _logger.info("Deleted %d old categories", cr.rowcount)

    cr.execute("DELETE FROM personal_budget_contributor")
    _logger.info("Deleted %d old contributors", cr.rowcount)
