"""Force-refresh the backup report email template body_html.

The template lives in `data/hosting_backup_email_template.xml` under
`<data noupdate="1">` so a normal upgrade does NOT rewrite body_html.
v18.0.2.31.x reworks the template to render Restic snapshots in the
"Fichiers" column instead of "Aucun fichier"; we extract the new body_html
from the XML on disk and write it directly to the existing mail.template.
"""

import logging
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    template = env.ref(
        "hosting_management.email_template_backup_report",
        raise_if_not_found=False,
    )
    if not template:
        _logger.warning("Backup email template not found — nothing to refresh.")
        return

    path = get_module_resource(
        "hosting_management", "data", "hosting_backup_email_template.xml"
    )
    if not path:
        _logger.warning("Could not locate email template XML file.")
        return

    try:
        tree = ET.parse(path)
    except ET.ParseError:
        _logger.exception("Could not parse email template XML.")
        return

    record = tree.find(".//record[@id='email_template_backup_report']")
    if record is None:
        _logger.warning("Email template record not found in XML.")
        return

    body_field = record.find("./field[@name='body_html']")
    if body_field is None:
        _logger.warning("body_html field not found in template record.")
        return

    parts = []
    if body_field.text:
        parts.append(body_field.text)
    for child in body_field:
        parts.append(ET.tostring(child, encoding="unicode"))
    body_html = "".join(parts).strip()

    if not body_html:
        _logger.warning("Extracted body_html is empty — skipping write.")
        return

    # body_html is translatable (JSONB) — write once per installed language
    # so users on every locale see the updated template, not just the one
    # matching the migration's runtime context.
    langs = env["res.lang"].search([("active", "=", True)]).mapped("code")
    if not langs:
        langs = ["en_US"]
    for lang in langs:
        template.with_context(lang=lang).write({"body_html": body_html})
    _logger.info(
        "hosting_management 18.0.2.31.2: backup email template body_html "
        "refreshed (%d chars) for langs %s — Restic snapshots now in 'Fichiers'.",
        len(body_html),
        ", ".join(langs),
    )
