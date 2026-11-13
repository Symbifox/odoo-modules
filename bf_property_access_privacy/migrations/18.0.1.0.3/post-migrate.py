"""18.0.1.0.3 : l'avis, la finalité et la politique de conservation nomment ce qu'une
boîte à colis reliée reçoit en plus — un moyen de joindre le résident (lot 5).

Mêmes fiches en noupdate, même geste que 18.0.1.0.1 : on les réécrit depuis le fichier de
données, et seulement si l'organisation n'a pas déjà retouché l'avis elle-même.
"""
from lxml import etree

from odoo import SUPERUSER_ID, api
from odoo.modules.module import get_module_resource

FIELDS = {
    "purpose_property_access_control": ("plain_language_summary",),
    "notice_property_access_control": ("body_fr", "body_en"),
    "retention_property_access_control": ("data_categories",),
}
# Le texte livré par 18.0.1.0.1, mot pour mot : absent, c'est que l'organisation a
# écrit le sien, et on n'y touche pas.
ORIGINAL = "n'existe que dans l'appareil"
# Un fragment qui tient sur UNE ligne du fichier de données : un repère
# qui dépend d'un retour de ligne et de son indentation ne correspond
# jamais, et la migration échouerait ouverte, en silence.
DEJA = "votre numéro de mobile, ou les deux si"


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    notice = env.ref("bf_property_access_privacy.notice_property_access_control",
                     raise_if_not_found=False)
    if not notice:
        return
    body = notice.body_fr or ""
    if DEJA in body or ORIGINAL not in body:
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
