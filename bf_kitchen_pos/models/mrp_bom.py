from odoo import fields, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    bf_available_in_pos = fields.Boolean(
        related="product_tmpl_id.available_in_pos", readonly=False, string="On sale at the register")
    bf_pos_categ_ids = fields.Many2many(
        related="product_tmpl_id.pos_categ_ids", readonly=False, string="Register categories")
