from odoo import fields, models


class FoodAllergen(models.Model):
    _name = "bf.food.allergen"
    _description = "Food allergen"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Char(translate=True, help="What the allergen covers, for the menu sheet.")

    _sql_constraints = [
        ("name_unique", "unique(name)", "This allergen already exists."),
    ]
