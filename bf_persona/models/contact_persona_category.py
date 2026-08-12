from odoo import fields, models


class ContactPersonaCategory(models.Model):
    _name = "contact.persona.category"
    _description = "Catégorie de communication (pour règles c.c.)"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Identifiant technique (billing, support, ...)")
    sequence = fields.Integer(default=10)
    color = fields.Integer()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_unique", "unique(code)", "Le code de catégorie doit être unique."),
    ]
