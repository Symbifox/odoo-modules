from odoo import models


class ProjectTags(models.Model):
    _name = "project.tags"
    _inherit = ["project.tags", "bf.color.mixin"]


class ProjectProject(models.Model):
    _name = "project.project"
    _inherit = ["project.project", "bf.color.mixin"]


class ProjectTask(models.Model):
    _name = "project.task"
    _inherit = ["project.task", "bf.color.mixin"]
