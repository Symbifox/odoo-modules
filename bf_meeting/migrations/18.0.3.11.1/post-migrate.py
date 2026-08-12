"""Force-refresh meeting agenda + report mail.template body_html across langs."""

import logging
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


FILES_AND_RECORDS = [
    ("meeting_agenda_mail_template.xml", "meeting_agenda_mail_template"),
    ("meeting_report_mail_template.xml", "meeting_report_mail_template"),
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

    for xml_file, record_id in FILES_AND_RECORDS:
        path = get_module_resource("bf_meeting", "data", xml_file)
        if not path:
            continue
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            _logger.exception("Could not parse %s", xml_file)
            continue

        template = env.ref("bf_meeting.%s" % record_id, raise_if_not_found=False)
        if not template:
            continue

        record = tree.find(".//record[@id='%s']" % record_id)
        if record is None:
            continue

        updates = {}
        for fname in ("body_html", "email_from", "subject"):
            v = _extract_field(record, fname)
            if v:
                updates[fname] = v
        if not updates:
            continue

        for lang in langs:
            template.with_context(lang=lang).write(updates)
        _logger.info(
            "bf_meeting 18.0.3.11.1: refreshed %s across %d langs",
            record_id,
            len(langs),
        )
