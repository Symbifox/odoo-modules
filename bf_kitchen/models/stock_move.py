from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# Written only by the Received button of a vendor bill, as superuser.
RECEIPT_MARKERS = ("bf_kitchen_bill_line_id",)


class StockMove(models.Model):
    _inherit = "stock.move"

    bf_kitchen_bill_line_id = fields.Many2one(
        "account.move.line", "Received from bill line", copy=False, readonly=True, index="btree_not_null",
        help="Set by the kitchen's Received button: the vendor bill line this receipt stands for.")

    def _bf_kitchen_refuse_markers(self):
        raise AccessError(_("The link between a receipt and a vendor bill line is set by the Received "
                            "button only."))

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        if not self.env.su and any(m.bf_kitchen_bill_line_id for m in moves):
            moves._bf_kitchen_refuse_markers()
        return moves

    def write(self, vals):
        if not self.env.su and set(RECEIPT_MARKERS) & set(vals):
            self._bf_kitchen_refuse_markers()
        return super().write(vals)

    def _bf_kitchen_net_qty(self):
        """Quantity this done move brought in and that is still in, in the product's stock unit:
        its quantity minus what was returned from it (a return of a return counts back)."""
        self.ensure_one()
        if self.state != "done":
            return 0.0
        qty = self.product_uom._compute_quantity(self.quantity, self.product_id.uom_id, round=False)
        for ret in self.returned_move_ids:
            qty -= ret._bf_kitchen_net_qty()
        return qty

    def _should_force_price_unit(self):
        """A receipt made by Received is valued at its own price, the bill line's cost per stock
        unit (the move price is not rounded). Odoo otherwise takes a price that is zero at the 2
        decimals of Product Price (0.002 $/g) for no price, and values the receipt at the
        product's standard cost."""
        return bool(self.bf_kitchen_bill_line_id) or super()._should_force_price_unit()
