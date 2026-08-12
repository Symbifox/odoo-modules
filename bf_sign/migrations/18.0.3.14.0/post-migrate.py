"""Backfill the per-signer opening timestamps from the audit trail.

v18.0.3.14.0 records when a signer opens the document on the signer record
itself (``first_viewed_on`` / ``last_viewed_on`` / ``view_count``), so the
question "has this person even looked at it" is answerable from the list
instead of by reading the journal.

Without this, every request that predates the upgrade would read as "never
opened", which is not merely incomplete — it is wrong, and the journal already
holds the truth. ``bf.sign.log`` is append-only, so the ``viewed`` events are
authoritative and complete.

Matching is on ``actor`` (the signer email, which is what ``register_signer_view``
writes) scoped to the request, so two signers on the same request are not
conflated. ``timestamp_utc`` is stored as an ISO 8601 string.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        WITH views AS (
            SELECT s.id AS signer_id,
                   MIN(l.timestamp_utc) AS first_seen,
                   MAX(l.timestamp_utc) AS last_seen,
                   COUNT(*)             AS n
              FROM bf_sign_log l
              JOIN bf_sign_signer s
                ON s.request_id = l.request_id
               AND lower(s.email) = lower(l.actor)
             WHERE l.event = 'viewed'
               AND l.actor IS NOT NULL
             GROUP BY s.id
        )
        UPDATE bf_sign_signer s
           SET first_viewed_on = v.first_seen::timestamptz AT TIME ZONE 'UTC',
               last_viewed_on  = v.last_seen::timestamptz AT TIME ZONE 'UTC',
               view_count      = v.n
          FROM views v
         WHERE v.signer_id = s.id
    """)
    backfilled = cr.rowcount

    # A signer who reached 'viewed'/'signed' necessarily opened the document,
    # even if no matching journal line survives (e.g. an email edited after the
    # fact, so `actor` no longer matches). Flag those without inventing a date.
    cr.execute("""
        UPDATE bf_sign_signer
           SET view_count = GREATEST(view_count, 1)
         WHERE first_viewed_on IS NULL
           AND state IN ('viewed', 'signed')
    """)
    undated = cr.rowcount

    cr.execute("""
        UPDATE bf_sign_signer
           SET has_viewed = (first_viewed_on IS NOT NULL)
    """)
    # The request-level rollups are stored computes; raw SQL above bypassed the
    # ORM, so nothing marked them dirty. Recompute them explicitly.
    env = api.Environment(cr, SUPERUSER_ID, {})
    requests = env["bf.sign.request"].search([])
    if requests:
        requests._compute_viewed()
        requests.flush_recordset(["viewed_count", "last_viewed_on", "view_status"])

    _logger.info(
        "bf_sign: ouverture rapatriée du journal pour %s signataire(s) ; "
        "%s ont ouvert sans ligne de journal appariable (sans date) ; "
        "%s demande(s) recalculées.",
        backfilled, undated, len(requests))
