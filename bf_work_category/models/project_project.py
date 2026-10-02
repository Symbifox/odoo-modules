from odoo import models


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "bf.work.category.mixin"]

    _bf_work_category_sources = (("tags", "tag_ids"),)
