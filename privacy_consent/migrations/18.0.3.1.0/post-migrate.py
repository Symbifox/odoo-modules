"""Post-migration for 18.0.3.1.0.

- Backfills `previous_hash` on existing privacy.destruction.register entries
  to establish the chain on legacy records.
- Backfills `document_date` on existing privacy.document.classification rows
  from `classified_at` so retention_expiry_date keeps its historical value
  (migration is behavior-preserving; users can later set real document dates).
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # Backfill document_date from classified_at
    cr.execute(
        """
        UPDATE privacy_document_classification
        SET document_date = classified_at::date
        WHERE document_date IS NULL
          AND classified_at IS NOT NULL
        """
    )
    _logger.info("Backfilled document_date on %s classifications", cr.rowcount)

    # Rebuild hash chain on register entries (in id order, per company)
    Register = env["privacy.destruction.register"]
    entries = Register.search([], order="company_id, id")
    prior_by_company = {}
    for entry in entries:
        prior_hash = prior_by_company.get(entry.company_id.id, "")
        new_hash = entry._compute_verification_hash(previous_hash=prior_hash)
        cr.execute(
            """
            UPDATE privacy_destruction_register
            SET previous_hash = %s, verification_hash = %s
            WHERE id = %s
            """,
            (prior_hash, new_hash, entry.id),
        )
        prior_by_company[entry.company_id.id] = new_hash
    _logger.info("Rebuilt hash chain on %s register entries", len(entries))
