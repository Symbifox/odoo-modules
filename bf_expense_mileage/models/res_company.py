# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_mileage_product_id = fields.Many2one(
        "product.product", string="Full-rate mileage category", domain=[("can_be_expensed", "=", True)],
        help="Expense category of the kilometres up to the yearly threshold. Its cost is the rate.")
    bf_mileage_reduced_product_id = fields.Many2one(
        "product.product", string="Reduced-rate mileage category", domain=[("can_be_expensed", "=", True)],
        help="Expense category of the kilometres beyond the yearly threshold. Its cost is the rate.")
    bf_mileage_threshold = fields.Integer(
        string="Yearly kilometres at the full rate", default=5000,
        help="Per employee and calendar year, counting every mileage expense of the year. "
             "0 = the full rate for every kilometre.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_mileage_product_id = fields.Many2one(
        related="company_id.bf_mileage_product_id", readonly=False)
    bf_mileage_rate = fields.Float(
        related="company_id.bf_mileage_product_id.standard_price", readonly=False,
        string="Full rate per km", help="The cost of the expense category, per kilometre, in this company.")
    bf_mileage_reduced_product_id = fields.Many2one(
        related="company_id.bf_mileage_reduced_product_id", readonly=False)
    bf_mileage_reduced_rate = fields.Float(
        related="company_id.bf_mileage_reduced_product_id.standard_price", readonly=False,
        string="Reduced rate per km", help="The cost of the expense category, per kilometre, in this company.")
    bf_mileage_threshold = fields.Integer(
        related="company_id.bf_mileage_threshold", readonly=False)
