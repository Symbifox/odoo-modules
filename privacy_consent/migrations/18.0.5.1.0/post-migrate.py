"""Post-migration: consent emails in one language, with the tenant's colours and address.

Found by a QA with real emails read in a mailbox (2026-09-27):
every consent email was bilingual French/English, its button was a hardcoded grey
(#32373c), its privacy contact was the placeholder privacy@example.com on every tenant
installed after 18.0.2.5.0, and its links opened in the visitor's browser language.

The six templates are ``noupdate="1"``: a ``-u`` does not rewrite them. This migration
writes the shipped subject, lang and body into every ACTIVE language of each template,
**only if the template still is the shipped bilingual one**: its subject carries " / " and
its body both "Bonjour" and "Hello". A tenant's own version is left alone and logged.
Measured before shipping (2026-09-27): one tenant had rewritten its consent request as a
short French email pointing to the secure page; it is kept. Every other template measured
still was the shipped one.
"""
import logging
import os

from lxml import etree

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

FILES = ("mail_template.xml", "mail_template_sequence.xml")


def _records(module_dir):
    for name in FILES:
        tree = etree.parse(os.path.join(module_dir, "data", name))
        for record in tree.xpath('//record[@model="mail.template"]'):
            values = {}
            for field in record.findall("field"):
                fname = field.get("name")
                if fname not in ("subject", "lang", "body_html"):
                    continue
                if field.get("type") == "html":
                    values[fname] = (field.text or "") + "".join(
                        etree.tostring(child, encoding="unicode") for child in field)
                else:
                    values[fname] = field.text or ""
            yield "privacy_consent.%s" % record.get("id"), values


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    module_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    langs = env["res.lang"].search([]).mapped("code") or ["en_US"]
    for xmlid, values in _records(module_dir):
        template = env.ref(xmlid, raise_if_not_found=False)
        if not template:
            _logger.warning("privacy_consent 18.0.5.1.0: %s not found, skipped", xmlid)
            continue
        if not _is_shipped_bilingual(template, langs):
            _logger.warning("privacy_consent 18.0.5.1.0: %s is the tenant's own version, kept", xmlid)
            continue
        for lang in langs:
            template.with_context(lang=lang).write(values)
        _logger.info("privacy_consent 18.0.5.1.0: %s rewritten in %s", xmlid, ", ".join(langs))


def _is_shipped_bilingual(template, langs):
    """The shipped template said everything twice; a tenant's own version does not."""
    subjects = " ".join(template.with_context(lang=lang).subject or "" for lang in langs)
    bodies = " ".join(template.with_context(lang=lang).body_html or "" for lang in langs)
    return " / " in subjects and "Bonjour" in bodies and "Hello" in bodies
