# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """
    Pre-migration: Create hosting.server records from existing docker_host values.

    This preserves the old docker_host column data before it becomes computed.
    We store the mapping in a temporary column for post-migrate to use.
    """
    if not version:
        return

    _logger.info("Pre-migration 18.0.2.5.0: Preserving docker_host data")

    # Check if docker_host column exists (may have been removed in later versions)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'hosting_service' AND column_name = 'docker_host'
    """)
    if not cr.fetchone():
        _logger.info("Column docker_host does not exist, skipping migration")
        return

    # Add a temporary column to store the old docker_host value
    cr.execute("""
        ALTER TABLE hosting_service
        ADD COLUMN IF NOT EXISTS _old_docker_host VARCHAR
    """)

    # Copy the current docker_host values to the temporary column
    cr.execute("""
        UPDATE hosting_service
        SET _old_docker_host = docker_host
        WHERE docker_host IS NOT NULL AND docker_host != ''
    """)

    # Get distinct docker_host values to create servers
    try:
        cr.execute("""
            SELECT DISTINCT docker_host
            FROM hosting_service
            WHERE docker_host IS NOT NULL AND docker_host != ''
        """)
        hosts = cr.fetchall()
    except Exception:
        _logger.info("Could not query docker_host, skipping migration")
        return

    if not hosts:
        _logger.info("No existing docker_host values found, skipping server creation")
        return

    _logger.info("Found %d unique docker_host values to migrate", len(hosts))

    # Create hosting.server records for each unique host
    for (hostname,) in hosts:
        # Generate a code from the hostname
        # e.g., "server1.example.com" -> "SERVER1"
        code = hostname.split(".")[0].upper().replace("-", "")[:10]
        name = hostname.split(".")[0].replace("-", " ").title()

        # Check if server already exists (by hostname)
        cr.execute("""
            SELECT id FROM hosting_server WHERE hostname = %s
        """, (hostname,))
        existing = cr.fetchone()

        if existing:
            _logger.info("Server with hostname '%s' already exists, skipping", hostname)
            continue

        # Check if code is unique, append number if not
        base_code = code
        counter = 1
        while True:
            cr.execute("""
                SELECT id FROM hosting_server WHERE code = %s
            """, (code,))
            if not cr.fetchone():
                break
            code = f"{base_code}{counter}"
            counter += 1

        _logger.info("Creating server: %s (%s) - %s", name, code, hostname)

        cr.execute("""
            INSERT INTO hosting_server (
                name, code, hostname, server_type, state,
                ssh_user, ssh_port, active, create_date, write_date
            ) VALUES (
                %s, %s, %s, 'production', 'active',
                'root', 22, true, NOW(), NOW()
            )
        """, (name, code, hostname))

    _logger.info("Pre-migration 18.0.2.5.0 completed")
