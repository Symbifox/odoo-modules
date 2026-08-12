"""Re-load `mail_template_client_maintenance_notice` body_html in all langs.

2026-05-17 harmonisation visuelle : remplacement des accents ambre
(#F59E0B / #FDE68A / #FFFBEB / #92400E) par `brand_primary` + neutres
(#E5E7EB / #F9FAFB / #6B7280), pour aligner le template d'avis de
maintenance sur les autres templates client (générique, mensuel,
intervention).

Comme pour 18.0.2.36.1, Odoo n'écrit la nouvelle valeur que dans le slot
JSONB de la langue courante au moment du `-u`, donc on force le refresh
sur chaque `res.lang` actif.
"""

import logging
from xml.etree import ElementTree as ET

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

_logger = logging.getLogger(__name__)


XML_FILE = "hosting_client_email_templates.xml"
RECORD_ID = "mail_template_client_maintenance_notice"


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

    path = get_module_resource("hosting_management", "data", XML_FILE)
    if not path:
        _logger.warning("Could not locate %s — skip", XML_FILE)
        return
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        _logger.exception("Failed to parse %s", XML_FILE)
        return

    record = tree.find(".//record[@id='%s']" % RECORD_ID)
    if record is None:
        _logger.warning("Record %s not found in %s", RECORD_ID, XML_FILE)
        return

    template = env.ref(
        "hosting_management.%s" % RECORD_ID, raise_if_not_found=False
    )
    if not template:
        _logger.warning("Template hosting_management.%s not in DB", RECORD_ID)
        return

    body = _extract_field(record, "body_html")
    if not body:
        return

    for lang in langs:
        template.with_context(lang=lang).write({"body_html": body})

    _logger.info(
        "hosting_management 18.0.2.41.0: refreshed %s body_html across %d langs",
        RECORD_ID,
        len(langs),
    )
