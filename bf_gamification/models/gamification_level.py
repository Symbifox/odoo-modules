from odoo import api, fields, models


class GamificationLevel(models.Model):
    _name = 'bf.gamification.level'
    _description = 'Niveau de gamification'
    _order = 'min_xp asc'

    name = fields.Char(string="Nom", required=True)
    sequence = fields.Integer(string="Séquence", default=10)
    min_xp = fields.Integer(string="XP minimum", required=True, default=0)
    icon = fields.Image(string="Icône", max_width=128, max_height=128)
    badge_image = fields.Image(string="Image de niveau", max_width=256, max_height=256)
    color = fields.Integer(string="Couleur")
    title = fields.Char(string="Titre", required=True)
    css_class = fields.Char(string="Classe CSS")

    _sql_constraints = [
        ('min_xp_unique', 'UNIQUE(min_xp)', 'Chaque niveau doit avoir un XP minimum unique.'),
    ]
