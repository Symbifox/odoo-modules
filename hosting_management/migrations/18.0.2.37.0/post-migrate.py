# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Post-migration : seed defaults for the new "Rapports de sauvegarde"
configuration block and disable the controller's inline send so the new
scheduled cron is the single source of email."""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env["ir.config_parameter"].sudo()

    defaults = {
        "hosting.backup_report_enabled": "1",
        "hosting.backup_report_mode": "scheduled",
        "hosting.backup_report_send_hour": "6",
        "hosting.backup_report_timezone": "America/Toronto",
        "hosting.backup_report_only_on_issues": "0",
    }
    for key, value in defaults.items():
        if not ICP.get_param(key):
            ICP.set_param(key, value)

    # Default recipient = company email if nothing is set yet — keeps existing
    # behavior intact while making the field visible/editable in the UI.
    if not ICP.get_param("hosting.backup_report_recipients"):
        company = env["res.company"].search([], limit=1, order="id")
        if company and company.email:
            ICP.set_param("hosting.backup_report_recipients", company.email)

    # In scheduled mode, the controller's inline send must be off so we don't
    # send twice (once on POST, once via cron).
    if ICP.get_param("hosting.backup_report_mode") == "scheduled":
        ICP.set_param("hosting.restic_send_email_report", "0")

    _logger.info(
        "hosting_management 18.0.2.37.0 : configuration des rapports de sauvegarde initialisée."
    )
