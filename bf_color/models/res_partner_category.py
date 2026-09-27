from odoo import models


class ResPartnerCategory(models.Model):
    _name = "res.partner.category"
    _inherit = ["res.partner.category", "bf.color.mixin"]
