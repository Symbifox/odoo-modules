from odoo import fields, models


class AuditElement(models.Model):
    _name = "audit.element"
    _description = "IT audit element"
    _order = "sequence, id"

    num = fields.Integer(string="Number", required=True)
    # Traduisibles depuis 18.0.1.17.5 : la grille livrée se lit dans la
    # langue de qui l'ouvre (source anglaise, français au catalogue fr_CA).
    name = fields.Char(string="Name", required=True, translate=True)
    description = fields.Text(string="Description", translate=True)
    best_practices = fields.Html(string="Law 25 best practices", translate=True)
    sequence = fields.Integer(string="Sequence", default=10)

    _sql_constraints = [
        ("num_unique", "unique(num)", "The element number must be unique."),
    ]
