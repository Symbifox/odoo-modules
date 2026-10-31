from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bf_kitchen_invoice_id = fields.Many2one(
        "account.move", "Invoice consumed", readonly=True, copy=False, index="btree_not_null",
        help="The customer invoice with no sales order whose dishes this consumption took out.")

    @api.model
    def _bf_kitchen_server_fields(self):
        return super()._bf_kitchen_server_fields() | {"bf_kitchen_invoice_id"}

    def _bf_kitchen_still_out(self):
        """These consumptions still have food out: some move not returned in full. A return
        (into the stock) is not a consumption."""
        return self.filtered(lambda p: p.state == "done" and any(
            m.location_dest_id.usage == "customer" and m._bf_kitchen_net_qty() > 0 for m in p.move_ids))
