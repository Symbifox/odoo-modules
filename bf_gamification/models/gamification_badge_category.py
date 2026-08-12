from odoo import fields, models


class GamificationBadgeCategory(models.Model):
    _name = 'bf.gamification.badge.category'
    _description = 'Catégorie de badge'
    _order = 'sequence, name'

    name = fields.Char(string="Nom", required=True)
    icon = fields.Image(string="Icône", max_width=64, max_height=64)
    sequence = fields.Integer(string="Séquence", default=10)
    color = fields.Integer(string="Couleur")
    description = fields.Text(string="Description")
    badge_ids = fields.One2many('bf.gamification.badge', 'category_id', string="Badges")
