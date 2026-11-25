from odoo import fields, models


class ContactPersonaCategory(models.Model):
    _name = "contact.persona.category"
    _description = "Communication category (for cc rules)"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True, string="Category")
    code = fields.Char(required=True, help="Technical identifier "
                                           "(billing, support, ...)", string="Code")
    sequence = fields.Integer(default=10, string="Sequence")
    color = fields.Integer(string="Colour")
    active = fields.Boolean(default=True, string="Active")

    _sql_constraints = [
        ("code_unique", "unique(code)", "The category code must be unique."),
    ]
