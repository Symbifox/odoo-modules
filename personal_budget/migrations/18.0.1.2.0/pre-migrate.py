"""Backfill `month = 0` on existing personal.budget.plan rows.

Before 18.0.1.2.0 the plan model had (year, category_id) unique. We're
adding a `month` column where `0` means "annual lump sum" (existing
behavior) and `1..12` means "month-specific override".

Every existing row is annual → set month = 0. The new UNIQUE constraint
becomes (year, month, category_id); since each (year, category_id) was
already unique, no duplicate is possible. Defensive: log any duplicates
that somehow exist so we can react before the constraint is applied.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # Add column with default 0 (Odoo's ORM upgrade will also add it, but
    # doing it here ensures the backfill runs before any constraint is
    # enforced on the new column).
    cr.execute("""
        ALTER TABLE personal_budget_plan
        ADD COLUMN IF NOT EXISTS month INTEGER DEFAULT 0
    """)
    cr.execute("""
        UPDATE personal_budget_plan SET month = 0 WHERE month IS NULL
    """)
    updated = cr.rowcount
    _logger.info("personal_budget: backfilled month=0 on %d plan rows", updated)

    # Defensive duplicate check before the new UNIQUE constraint kicks in
    cr.execute("""
        SELECT year, month, category_id, COUNT(*) AS n
        FROM personal_budget_plan
        GROUP BY year, month, category_id
        HAVING COUNT(*) > 1
    """)
    duplicates = cr.fetchall()
    if duplicates:
        _logger.warning(
            "personal_budget: %d duplicate (year, month, category) groups "
            "found — the new UNIQUE constraint will FAIL. Resolve manually:",
            len(duplicates),
        )
        for year, month, category_id, n in duplicates[:20]:
            _logger.warning(
                "  year=%s month=%s category_id=%s count=%s",
                year, month, category_id, n,
            )
