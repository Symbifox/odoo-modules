from odoo import models


class PurchaseOrder(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # Standard purchase order / RFQ PDF.
        return "purchase.action_report_purchase_order"
