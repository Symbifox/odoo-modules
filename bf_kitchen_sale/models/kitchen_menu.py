from odoo import _, api, models

from odoo.addons.bf_kitchen.models.kitchen_consumption import SALE_TYPES


class KitchenMenu(models.TransientModel):
    _inherit = "bf.kitchen.menu"

    @api.model
    def _bf_menu_sales(self, company, start, end, warehouse=None):
        """The invoiced sales: lines of the customer invoices and credit notes posted (a sales order's and an
        invoice's without one), in the company's currency. An invoice of a register order is left out: the
        register counts that sale.

        A plain credit note is a commercial gesture: the food was served, it lowers the sales, not the
        portions. A credit note that cancels its invoice (the modify mode) takes the portions back too.

        By day of invoice for the menu. With a warehouse (the food cost of a period between two counts): an
        invoice without a sales order by the moment its food left that warehouse, as the stock moves are
        read; a sales order's lines and the credit notes by their day, when they belong to that warehouse."""
        sales = super()._bf_menu_sales(company, start, end, warehouse=warehouse)
        AML = self.env["account.move.line"].sudo()
        base = [("move_id.company_id", "=", company.id), ("parent_state", "=", "posted"),
                ("display_type", "=", "product"), ("product_id.bf_kitchen_type", "in", SALE_TYPES)]
        first, last = self._bf_local_days(company, start, end)
        by_day = [("move_id.invoice_date", ">=", first), ("move_id.invoice_date", "<=", last)]
        if not warehouse:
            lines = AML.search(base + by_day + [("move_id.move_type", "in", ("out_invoice", "out_refund"))])
        else:
            kitchen = company._bf_kitchen_warehouse()
            dated = AML.search(base + by_day + [("move_id.move_type", "in", ("out_invoice", "out_refund"))])
            dated = dated.filtered(
                lambda l: warehouse in l.sale_line_ids.order_id.warehouse_id if l.sale_line_ids
                else (l.move_id.move_type == "out_refund" and warehouse == kitchen))
            direct = AML.search(base + [
                ("move_id.move_type", "=", "out_invoice"), ("sale_line_ids", "=", False),
                ("move_id.bf_kitchen_consumption_ids.date_done", ">", start),
                ("move_id.bf_kitchen_consumption_ids.date_done", "<=", end),
                ("move_id.bf_kitchen_consumption_ids.picking_type_id.warehouse_id", "=", warehouse.id)])
            lines = dated | direct
        if "pos_order_ids" in self.env["account.move"]._fields:
            lines = lines.filtered(lambda l: not l.move_id.pos_order_ids)
        label = _("Invoices")
        for line in lines:
            move = line.move_id
            qty = line.product_uom_id._compute_quantity(line.quantity, line.product_id.uom_id, round=False) \
                if line.product_uom_id else line.quantity
            if move.move_type == "out_refund":
                qty = -qty if move.bf_kitchen_cancels else 0.0
            # The balance is in the company's currency, negative for income.
            self._bf_menu_add(sales, line.product_id, qty, -line.balance, label)
        return sales
