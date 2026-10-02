from odoo import models


class BfTrainingCategory(models.Model):
    _name = "bf.training.category"
    _inherit = ["bf.training.category", "bf.color.mixin"]
