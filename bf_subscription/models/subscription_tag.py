from odoo import fields, models


class SubscriptionTag(models.Model):
    _name = 'subscription.tag'
    _description = "Étiquette d'abonnement"
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=0)

    _sql_constraints = [
        ('name_unique', 'unique(name)', "Cette étiquette existe déjà."),
    ]
