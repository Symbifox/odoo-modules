# -*- coding: utf-8 -*-
from random import randint

from odoo import fields, models


class BfOutreachTag(models.Model):
    _name = "bf.outreach.tag"
    _description = "Outreach tag"
    _order = "name"

    def _default_color(self):
        return randint(1, 11)

    name = fields.Char(string="Tag", required=True, translate=True)
    color = fields.Integer(string="Color", default=_default_color)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("name_uniq", "unique (name)", "This tag already exists."),
    ]
