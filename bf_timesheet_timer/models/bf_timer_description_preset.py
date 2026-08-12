from odoo import fields, models


class BfTimerDescriptionPreset(models.Model):
    _name = "bf.timer.description.preset"
    _description = "Preset de description pour timer"
    _order = "sequence, id"

    name = fields.Char(required=True, string="Nom")
    text = fields.Char(required=True, string="Texte")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
