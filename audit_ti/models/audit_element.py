from odoo import fields, models


class AuditElement(models.Model):
    _name = "audit.element"
    _description = "Élément d'audit TI"
    _order = "sequence, id"

    num = fields.Integer(string="Numéro", required=True)
    name = fields.Char(string="Nom", required=True)
    description = fields.Text(string="Description")
    best_practices = fields.Html(string="Meilleures pratiques Loi 25")
    sequence = fields.Integer(string="Séquence", default=10)

    _sql_constraints = [
        ("num_unique", "unique(num)", "Le numéro d'élément doit être unique."),
    ]
