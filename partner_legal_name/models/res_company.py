from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    legal_name = fields.Char(
        related="partner_id.legal_name",
        string="Legal name",
        readonly=False,
        store=True,
    )
