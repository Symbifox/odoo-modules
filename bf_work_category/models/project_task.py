from odoo import models


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "bf.work.category.mixin"]

    _bf_work_category_sources = (("tags", "tag_ids"), ("record", "project_id"))
