from odoo import fields, models


class SubscriptionTag(models.Model):
    _name = 'subscription.tag'
    _description = "Subscription tag"
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=0)

    _sql_constraints = [
        ('name_unique', 'unique(name)', "This tag already exists."),
    ]
