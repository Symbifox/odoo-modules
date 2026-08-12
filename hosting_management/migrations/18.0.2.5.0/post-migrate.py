# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Post-migration: Link services to servers based on the preserved docker_host values.

    Uses the _old_docker_host temporary column created in pre-migrate.
    """
    if not version:
        return

    _logger.info("Post-migration 18.0.2.5.0: Linking services to servers")

    # Check if the temporary column exists
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'hosting_service' AND column_name = '_old_docker_host'
    """)
    if not cr.fetchone():
        _logger.info("No _old_docker_host column found, skipping service linking")
        return

    # Link services to servers based on hostname match
    cr.execute("""
        UPDATE hosting_service hs
        SET server_id = srv.id
        FROM hosting_server srv
        WHERE hs._old_docker_host = srv.hostname
          AND hs._old_docker_host IS NOT NULL
          AND hs._old_docker_host != ''
          AND hs.server_id IS NULL
    """)
    linked_count = cr.rowcount
    _logger.info("Linked %d services to servers", linked_count)

    # Drop the temporary column
    cr.execute("""
        ALTER TABLE hosting_service
        DROP COLUMN IF EXISTS _old_docker_host
    """)

    _logger.info("Post-migration 18.0.2.5.0 completed")
