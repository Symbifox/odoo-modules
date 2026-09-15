from odoo import fields, models


class ContactPersonaCategory(models.Model):
    _name = "contact.persona.category"
    _description = "Catégorie de communication (pour règles c.c.)"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True, string="Catégorie")
    code = fields.Char(required=True, help="Identifiant technique (billing, support, ...)", string="Code")
    sequence = fields.Integer(default=10, string="Séquence")
    color = fields.Integer(string="Couleur")
    active = fields.Boolean(default=True, string="Active")

    _sql_constraints = [
        ("code_unique", "unique(code)", "Le code de catégorie doit être unique."),
    ]
