# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _populate_existing_storage_configs(cr):
    """Populate storage_check_type and related fields on existing services.

    This migration is instance-specific. The service codes, container names,
    and database credentials below were sanitized for publication.
    Adapt to your own infrastructure before running.
    """

    # Container directory checks
    # Format: (service_code, mount_path, optional_docker_container_override)
    directory_configs = [
        # ("HST-XXXX", "/path/to/data", None),
        # ("HST-XXXX", "/data", "client-vaultwarden"),
    ]

    for code, path, container_override in directory_configs:
        if container_override:
            cr.execute(
                """
                UPDATE hosting_service
                SET storage_check_type = 'container_directory',
                    storage_check_path = %s,
                    docker_container = %s
                WHERE code = %s
                """,
                (path, container_override, code),
            )
        else:
            cr.execute(
                """
                UPDATE hosting_service
                SET storage_check_type = 'container_directory',
                    storage_check_path = %s
                WHERE code = %s
                """,
                (path, code),
            )
        if cr.rowcount:
            _logger.info("Configured container_directory for %s", code)

    # PostgreSQL checks — docker_container MUST point to the DB container, not the app
    # Format: (service_code, db_name, db_user, db_container)
    postgres_configs = [
        # ("HST-XXXX", "mydb", "myuser", "myapp-db"),
    ]

    for code, db_name, db_user, db_container in postgres_configs:
        cr.execute(
            """
            UPDATE hosting_service
            SET storage_check_type = 'postgres',
                storage_check_db_name = %s,
                storage_check_db_user = %s,
                docker_container = %s
            WHERE code = %s
            """,
            (db_name, db_user, db_container, code),
        )
        if cr.rowcount:
            _logger.info("Configured postgres for %s", code)

    # Nextcloud DB checks — queries oc_filecache, auto-discovers credentials
    # Format: (service_code, db_container)
    nc_db_configs = [
        # ("HST-XXXX", "client-nc-db"),
    ]

    for code, db_container in nc_db_configs:
        cr.execute(
            """
            UPDATE hosting_service
            SET storage_check_type = 'nextcloud_db',
                docker_container = %s
            WHERE code = %s
            """,
            (db_container, code),
        )
        if cr.rowcount:
            _logger.info("Configured nextcloud_db for %s", code)


def _create_new_services(cr, env):
    """Create new hosting.service records for previously untracked services.

    This migration is instance-specific. Service names, partner lookups,
    and container references were sanitized for publication.
    """
    _logger.info("Skipping instance-specific service creation (sanitized for publication)")


def migrate(cr, version):
    _logger.info("Post-migrate: populating storage config on existing services")
    _populate_existing_storage_configs(cr)

    _logger.info("Post-migrate: creating new hosting services")
    env = api.Environment(cr, SUPERUSER_ID, {})
    _create_new_services(cr, env)
