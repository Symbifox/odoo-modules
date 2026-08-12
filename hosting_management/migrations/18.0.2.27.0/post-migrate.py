# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging
import os

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Optionally upsert hosting.backup.repository records from a JSON file.

    The path is configurable via ir.config_parameter
    `hosting.restic_repos_json_path` (default: /mnt/restic-config/repos.json).
    If the file does not exist, this is a no-op (the XML data file already
    seeded the bootstrap records on first install).
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    path = (
        env["ir.config_parameter"]
        .sudo()
        .get_param(
            "hosting.restic_repos_json_path", "/mnt/restic-config/repos.json"
        )
    )
    if not path or not os.path.isfile(path):
        _logger.info(
            "Restic repos.json not found at %s — skipping dynamic upsert "
            "(XML bootstrap remains authoritative)",
            path,
        )
        return

    try:
        with open(path, encoding="utf-8") as f:
            repos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("Could not read %s: %s — skipping", path, e)
        return

    Repo = env["hosting.backup.repository"]
    created = 0
    updated = 0
    for name, cfg in repos.items():
        retention = cfg.get("retention", {})
        vals = {
            "name": name,
            "s3_url": cfg.get("url") or False,
            "password_file_path": cfg.get("password_file") or False,
            "retention_daily": retention.get("keep_daily") or 14,
            "retention_weekly": retention.get("keep_weekly") or 8,
            "retention_monthly": retention.get("keep_monthly") or 12,
        }
        existing = Repo.search([("name", "=", name)], limit=1)
        if existing:
            existing.write(vals)
            updated += 1
        else:
            Repo.create(vals)
            created += 1

    _logger.info(
        "Restic repos sync from %s: %d created, %d updated", path, created, updated
    )
