"""Force-refresh project_knowledge_matrix mail.template body_html across langs."""

import logging
import os
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


FILES = [
    "document_mail_templates.xml",
    "document_report_mail_template.xml",
]


def _extract_field(record, field_name):
    field = record.find("./field[@name='%s']" % field_name)
    if field is None:
        return None
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

    for xml_file in FILES:
        path = get_module_resource("project_knowledge_matrix", "data", xml_file)
        if not path or not os.path.exists(path):
            continue
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            _logger.exception("Could not parse %s", xml_file)
            continue

        for record in tree.findall(".//record[@model='mail.template']"):
            rec_id = record.get("id")
            if not rec_id:
                continue
            template = env.ref(
                "project_knowledge_matrix.%s" % rec_id, raise_if_not_found=False
            )
            if not template:
                continue

            updates = {}
            for fname in ("body_html", "email_from", "email_to", "subject"):
                v = _extract_field(record, fname)
                if v:
                    updates[fname] = v
            if not updates:
                continue

            for lang in langs:
                template.with_context(lang=lang).write(updates)
            _logger.info(
                "project_knowledge_matrix 18.0.9.12.2: refreshed %s "
                "across %d langs",
                rec_id,
                len(langs),
            )
