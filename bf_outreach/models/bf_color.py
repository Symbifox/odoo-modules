from odoo import models


class BfOutreachTarget(models.Model):
    _name = "bf.outreach.target"
    _inherit = ["bf.outreach.target", "bf.color.mixin"]
