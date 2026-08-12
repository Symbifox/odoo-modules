"""Force-refresh the hour bank mail.template body_html across all langs."""

import logging
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


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

    path = get_module_resource("bf_hour_bank", "data", "hour_bank_mail_template.xml")
    if not path:
        return
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        _logger.exception("Could not parse hour_bank_mail_template.xml")
        return

    template = env.ref(
        "bf_hour_bank.email_template_hour_bank_report", raise_if_not_found=False
    )
    if not template:
        return

    record = tree.find(".//record[@id='email_template_hour_bank_report']")
    if record is None:
        return

    updates = {}
    for fname in ("body_html", "email_from", "email_to", "subject"):
        v = _extract_field(record, fname)
        if v:
            updates[fname] = v
    if not updates:
        return

    for lang in langs:
        template.with_context(lang=lang).write(updates)
    _logger.info(
        "bf_hour_bank 18.0.1.9.2: refreshed mail template across %d langs",
        len(langs),
    )
