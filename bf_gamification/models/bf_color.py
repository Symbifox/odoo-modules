from odoo import models


class BfGamificationBadgeCategory(models.Model):
    _name = "bf.gamification.badge.category"
    _inherit = ["bf.gamification.badge.category", "bf.color.mixin"]


class BfGamificationLevel(models.Model):
    _name = "bf.gamification.level"
    _inherit = ["bf.gamification.level", "bf.color.mixin"]
