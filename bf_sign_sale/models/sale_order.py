from odoo import models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # Standard Sales quotation / order PDF.
        return "sale.action_report_saleorder"
