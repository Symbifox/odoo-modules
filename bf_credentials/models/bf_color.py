from odoo import models


class ProjectCredentialType(models.Model):
    _name = "project.credential.type"
    _inherit = ["project.credential.type", "bf.color.mixin"]
