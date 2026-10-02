from odoo import models


class BfTimeOfDay(models.Model):
    _name = "bf.time.of.day"
    _inherit = ["bf.time.of.day", "bf.color.mixin"]
