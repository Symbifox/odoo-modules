from odoo import models


class BfVisit(models.Model):
    _name = "bf.visit"
    _inherit = ["bf.visit", "bf.color.mixin"]


class BfVisitListing(models.Model):
    _name = "bf.visit.listing"
    _inherit = ["bf.visit.listing", "bf.color.mixin"]
