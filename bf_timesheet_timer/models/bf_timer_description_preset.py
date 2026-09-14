from odoo import fields, models


class BfTimerDescriptionPreset(models.Model):
    _name = "bf.timer.description.preset"
    _description = "Timer description preset"
    _order = "sequence, id"

    name = fields.Char(required=True, string="Name")
    text = fields.Char(required=True, string="Text")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
