from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_food_supplier = fields.Boolean(
        "Food supplier",
        help="Its bill lines with no product are listed in Kitchen > Lines to attach.")
