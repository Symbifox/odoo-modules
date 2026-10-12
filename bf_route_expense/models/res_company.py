from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    bf_route_mileage_product_id = fields.Many2one(
        "product.product", string="Mileage, full rate", domain=[("can_be_expensed", "=", True)],
        help="Expense product of the kilometres up to the yearly threshold. Its cost is the rate.")
    bf_route_mileage_reduced_product_id = fields.Many2one(
        "product.product", string="Mileage, reduced rate", domain=[("can_be_expensed", "=", True)],
        help="Expense product of the kilometres beyond the yearly threshold. Its cost is the rate.")
    bf_route_mileage_threshold = fields.Integer(
        string="Kilometres at the full rate", default=5000,
        help="Per employee and calendar year, counting every mileage expense of the year. "
             "0 = the full rate for every kilometre.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_route_mileage_product_id = fields.Many2one(
        related="company_id.bf_route_mileage_product_id", readonly=False)
    bf_route_mileage_rate = fields.Float(
        related="company_id.bf_route_mileage_product_id.standard_price", readonly=False,
        string="Full rate", help="The cost of the expense product, per kilometre, in this company.")
    bf_route_mileage_reduced_product_id = fields.Many2one(
        related="company_id.bf_route_mileage_reduced_product_id", readonly=False)
    bf_route_mileage_reduced_rate = fields.Float(
        related="company_id.bf_route_mileage_reduced_product_id.standard_price", readonly=False,
        string="Reduced rate", help="The cost of the expense product, per kilometre, in this company.")
    bf_route_mileage_threshold = fields.Integer(
        related="company_id.bf_route_mileage_threshold", readonly=False)
