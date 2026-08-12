"""Force-refresh mail.template body_html across all installed langs.

2026-05-07 outer-wrapper-light patch introduced bare Jinja `{{ }}` inside
plain `href`/`src`/text in body_html. Odoo 18 renders body_html with QWeb
only — bare `{{ }}` is preserved literally, so backup/client/digest reports
shipped with visible `{{ (object.company_id.email or '') }}` and dead `#`
links since ~2026-05-06.

This migration:
  * extracts the corrected body_html from each XML on disk,
  * writes it to every installed `lang` JSONB slot (Odoo's `-u` only
    refreshes the runtime context's slot, and `noupdate="1"` records are
    skipped entirely), and
  * also refreshes `email_from`, `email_to`, `subject` since those changed.

Targets: backup report, hosting digest, and the 5 client templates
(welcome, monthly, maintenance, intervention, marketing).
"""

import logging
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


FILES_AND_RECORDS = [
    (
        "hosting_backup_email_template.xml",
        ["email_template_backup_report"],
    ),
    (
        "hosting_digest_template.xml",
        ["mail_template_hosting_digest"],
    ),
    (
        "hosting_client_email_templates.xml",
        [
            "mail_template_client_generic",
            "mail_template_client_monthly_report",
            "mail_template_client_maintenance_notice",
            "mail_template_client_intervention_report",
            "mail_template_client_welcome",
        ],
    ),
]


def _extract_field(record, field_name):
    field = record.find("./field[@name='%s']" % field_name)
    if field is None:
        return None
    if field.text and not list(field):
        return field.text
    parts = []
    if field.text:
        parts.append(field.text)
    for child in field:
        parts.append(ET.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = env["res.lang"].search([("active", "=", True)]).mapped("code") or ["en_US"]

    for xml_file, record_ids in FILES_AND_RECORDS:
        path = get_module_resource("hosting_management", "data", xml_file)
        if not path:
            _logger.warning("Could not locate %s — skip", xml_file)
            continue
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            _logger.exception("Failed to parse %s", xml_file)
            continue

        for rec_id in record_ids:
            record = tree.find(".//record[@id='%s']" % rec_id)
            if record is None:
                _logger.warning("Record %s not found in %s", rec_id, xml_file)
                continue

            template = env.ref(
                "hosting_management.%s" % rec_id, raise_if_not_found=False
            )
            if not template:
                _logger.warning("Template hosting_management.%s not in DB", rec_id)
                continue

            updates = {}
            for fname in ("body_html", "email_from", "email_to", "subject"):
                value = _extract_field(record, fname)
                if value:
                    updates[fname] = value

            if not updates:
                continue

            for lang in langs:
                template.with_context(lang=lang).write(updates)

            _logger.info(
                "hosting_management 18.0.2.36.0: refreshed %s "
                "(%s) across %d langs",
                rec_id,
                ", ".join(updates.keys()),
                len(langs),
            )
