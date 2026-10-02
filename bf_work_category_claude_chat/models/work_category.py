from odoo import models
from odoo.addons.bf_work_category.models.work_category_mixin import LINKED_SOURCES


class ClaudeChatSession(models.Model):
    _name = "claude.chat.session"
    _inherit = ["claude.chat.session", "bf.work.category.linked.mixin"]

    _bf_work_category_sources = LINKED_SOURCES
