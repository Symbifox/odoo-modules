from odoo import models


class ProjectDocumentType(models.Model):
    _name = "project.document.type"
    _inherit = ["project.document.type", "bf.color.mixin"]


class ProjectKnowledgeSection(models.Model):
    _name = "project.knowledge.section"
    _inherit = ["project.knowledge.section", "bf.color.mixin"]
