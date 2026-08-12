from odoo import fields, models


class BfNoteTag(models.Model):
    _name = "bf.note.tag"
    _description = "Étiquette de note"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=0)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("name_uniq", "unique(name)", "Cette étiquette existe déjà."),
    ]
