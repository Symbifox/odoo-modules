"""18.0.1.1.0: the mileage settings of the routes move to Mileage Tiers.

Before the upgrade drops the columns of ``bf_route_mileage_*`` (fields gone from the code), their
values go to ``bf_mileage_*`` of the same company, the settings that both modules now read.

⚠️ The routes' value wins where it is set, in the companies that have route days only. Mileage
Tiers copied it at its own install (where both were installed, the two sets are usually still
identical), or it is being installed in this very upgrade as the new
dependency, with its defaults: in both cases the routes' value is the one the route days have
been using. A company without route days keeps what its Expenses settings say (a threshold of 0
set there must not come back to 5,000). A NULL product on the
routes' side (a company that relied on the default) never erases the shared one. Each changed
value is logged as a WARNING: someone chose one of the two and lost the other.
"""
import logging

_logger = logging.getLogger(__name__)

PAIRS = (
    ("bf_route_mileage_product_id", "bf_mileage_product_id"),
    ("bf_route_mileage_reduced_product_id", "bf_mileage_reduced_product_id"),
    ("bf_route_mileage_threshold", "bf_mileage_threshold"),
)


def _columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", [table])
    return {row[0] for row in cr.fetchall()}


def migrate(cr, version):
    if not version:
        return
    columns = _columns(cr, "res_company")
    for source, target in PAIRS:
        if source not in columns:
            continue
        if target not in columns:
            # Mileage Tiers not loaded yet: it cannot be, it is a dependency loaded first.
            _logger.warning("bf_route_expense: %s missing, %s not migrated", target, source)
            continue
        cr.execute(f"""
            UPDATE res_company
               SET {target} = {source}
             WHERE {source} IS NOT NULL
               AND {target} IS DISTINCT FROM {source}
               AND id IN (SELECT DISTINCT company_id FROM bf_route_day WHERE company_id IS NOT NULL)
         RETURNING id, {source}
        """)
        for company_id, value in cr.fetchall():
            _logger.warning("bf_route_expense: company %s, %s = %s (from %s, which differed)",
                            company_id, target, value, source)
        # A company without route days keeps its Expenses value: say what the routes' side held.
        cr.execute(f"""
            SELECT id, {source}, {target} FROM res_company
             WHERE {source} IS NOT NULL
               AND {target} IS DISTINCT FROM {source}
               AND id NOT IN (SELECT DISTINCT company_id FROM bf_route_day WHERE company_id IS NOT NULL)
        """)
        for company_id, value, kept in cr.fetchall():
            _logger.warning("bf_route_expense: company %s has no route day: %s = %s kept, %s = %s dropped",
                            company_id, target, kept, source, value)
