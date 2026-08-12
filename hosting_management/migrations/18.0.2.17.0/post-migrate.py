# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Migrate existing domain_name values on hosting.service to hosting.domain records."""
    if not version:
        return

    _logger.info("Post-migrate 18.0.2.17.0: migrating domain_name to hosting.domain")

    # Check if domain_id column exists (should have been created by ORM)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'hosting_service' AND column_name = 'domain_id'
    """)
    if not cr.fetchone():
        _logger.warning("domain_id column not found on hosting_service, skipping migration")
        return

    # Get distinct domain names with their partner_ids
    cr.execute("""
        SELECT DISTINCT ON (LOWER(TRIM(domain_name)))
            LOWER(TRIM(domain_name)) AS domain_name,
            partner_id
        FROM hosting_service
        WHERE domain_name IS NOT NULL
          AND TRIM(domain_name) != ''
        ORDER BY LOWER(TRIM(domain_name)), id
    """)
    rows = cr.fetchall()

    if not rows:
        _logger.info("No domain_name values to migrate")
        return

    created = 0
    for domain_name, partner_id in rows:
        # Check if domain already exists
        cr.execute(
            "SELECT id FROM hosting_domain WHERE LOWER(name) = %s",
            (domain_name,),
        )
        existing = cr.fetchone()
        if existing:
            domain_id = existing[0]
        else:
            cr.execute("""
                INSERT INTO hosting_domain (name, partner_id, state, active, auto_renew,
                                            ssl_type, create_uid, write_uid,
                                            create_date, write_date)
                VALUES (%s, %s, 'active', true, false, 'none', 1, 1, NOW(), NOW())
                RETURNING id
            """, (domain_name, partner_id))
            domain_id = cr.fetchone()[0]
            created += 1

        # Link services with this domain_name to the new domain record
        cr.execute("""
            UPDATE hosting_service
            SET domain_id = %s
            WHERE LOWER(TRIM(domain_name)) = %s
              AND domain_id IS NULL
        """, (domain_id, domain_name))

    _logger.info(
        "Post-migrate 18.0.2.17.0: created %d hosting.domain records from %d unique domain names",
        created, len(rows),
    )
