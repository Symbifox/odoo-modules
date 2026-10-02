from odoo import models


class SubscriptionTag(models.Model):
    _name = "subscription.tag"
    _inherit = ["subscription.tag", "bf.color.mixin"]
