"""Feedback themes (outer loop).

The inner loop calls back the individual; the outer loop aggregates the
recurring causes and fixes them structurally. Themes are the aggregation
axis: tagged on feedbacks and complaints, pivoted in the existing reports,
reviewed periodically.
"""
from odoo import fields, models


class BfCxTheme(models.Model):
    _name = "bf.cx.theme"
    _description = "Thème de feedback"
    _order = "name"

    name = fields.Char(string="Thème", required=True, translate=True)
    color = fields.Integer(string="Couleur")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("name_uniq", "unique(name)", "Ce thème existe déjà."),
    ]
