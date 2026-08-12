# License LGPL-3.0 or later.
"""Backfill: run the new auto-matcher across all existing repos on upgrade.

Idempotent — only adds missing links, never removes existing ones.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Repo = env["hosting.backup.repository"]
    repos = Repo.search([("active", "=", True)])
    total = 0
    for repo in repos:
        try:
            total += repo.auto_link_services()
        except Exception:  # noqa: BLE001
            _logger.exception("Auto-link failed for repo %s", repo.name)
    _logger.info(
        "[18.0.2.38.0] Auto-linked %d service(s) across %d repo(s)",
        total,
        len(repos),
    )
