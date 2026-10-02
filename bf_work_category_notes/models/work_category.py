from odoo import models
from odoo.addons.bf_work_category.models.work_category_mixin import LINKED_SOURCES


class BfNote(models.Model):
    _name = "bf.note"
    _inherit = ["bf.note", "bf.work.category.linked.mixin"]

    _bf_work_category_sources = LINKED_SOURCES
