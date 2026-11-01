"""18.0.1.0.1 : l'avis, la finalité et la politique de conservation nomment la tablette
de l'entrée (bf_property_lobby). Ces fiches sont en noupdate : on les réécrit depuis le
fichier de données, mais seulement si l'organisation n'a pas déjà retouché l'avis."""
from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

FIELDS = {
    "purpose_property_access_control": ("plain_language_summary",),
    "notice_property_access_control": ("body_fr", "body_en"),
    "retention_property_access_control": ("data_categories",),
}
ORIGINAL = "supprimé avec le journal. Une"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    notice = env.ref("bf_property_access_privacy.notice_property_access_control", raise_if_not_found=False)
    if not notice or "tablette" in (notice.body_fr or "") or ORIGINAL not in (notice.body_fr or ""):
        return
    path = get_module_resource("bf_property_access_privacy", "data", "privacy_purpose_data.xml")
    tree = etree.parse(path)
    for xmlid, names in FIELDS.items():
        record = env.ref("bf_property_access_privacy." + xmlid, raise_if_not_found=False)
        node = tree.find(".//record[@id='%s']" % xmlid)
        if not record or node is None:
            continue
        vals = {}
        for name in names:
            field = node.find("field[@name='%s']" % name)
            if field is not None:
                vals[name] = (field.text or "").strip()
        record.write(vals)
