from odoo import models
from odoo.addons.bf_work_category.models.work_category_mixin import LINKED_SOURCES


class BfEmail(models.Model):
    _name = "bf.email"
    _inherit = ["bf.email", "bf.work.category.linked.mixin"]

    _bf_work_category_sources = LINKED_SOURCES
