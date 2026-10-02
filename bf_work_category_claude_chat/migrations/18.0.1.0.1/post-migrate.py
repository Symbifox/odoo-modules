from odoo import SUPERUSER_ID, api

from odoo.addons.bf_work_category.hooks import bf_work_category_load_translations


def migrate(cr, version):
    # 18.0.1.0.0 left this model's category origins untranslated: they belong to
    # bf_work_category, whose .po was loaded before they existed.
    bf_work_category_load_translations(api.Environment(cr, SUPERUSER_ID, {}))
