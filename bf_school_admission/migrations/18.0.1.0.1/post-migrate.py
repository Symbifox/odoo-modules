from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.tools import file_path
from odoo.tools.convert import xml_import

RECORD = "mail_template_admission_update"


def migrate(cr, version):
    """The application email gains the "fee to pay" step (public form, 2026-09-27).

    The template is in a `noupdate` block: an upgrade leaves the old body, and the family asked
    for the fee would receive an email with nothing but "Hello". This record alone is reloaded
    from the data file, then its translations, which followed the old English text.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    path = file_path("bf_school_admission/data/mail_template.xml")
    node = etree.parse(path).find(".//record[@id='%s']" % RECORD)
    # The record's own `noupdate` is checked again when it is loaded: lifted for this once.
    cr.execute("UPDATE ir_model_data SET noupdate = false WHERE module = 'bf_school_admission' AND name = %s",
               [RECORD])
    xml_import(env, "bf_school_admission", {}, "update", noupdate=False, xml_filename=path)._tag_record(node)
    # Before `noupdate` is put back: the importer does not overwrite a noupdate record's terms.
    langs = [code for code, __ in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms(["bf_school_admission"], langs, overwrite=True)
    cr.execute("UPDATE ir_model_data SET noupdate = true WHERE module = 'bf_school_admission' AND name = %s",
               [RECORD])
