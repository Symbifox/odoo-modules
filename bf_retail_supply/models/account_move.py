# -*- coding: utf-8 -*-
from odoo import models
from odoo.tools import float_compare


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        """A validated credit note from a supplier records what came back.

        The cost series is left alone: see the docstring of bf.retail.credit.
        """
        posted = super()._post(soft=soft)
        refunds = posted.filtered(lambda move: move.move_type == "in_refund")
        if refunds:
            refunds._bf_retail_write_credits()
        return posted

    def button_draft(self):
        result = super().button_draft()
        self._bf_retail_drop_credits()
        return result

    def button_cancel(self):
        result = super().button_cancel()
        self._bf_retail_drop_credits()
        return result

    def _bf_retail_drop_credits(self):
        """What came back is what a POSTED credit note says came back.

        A credit note sent back to draft, or cancelled, kept counting: the order guide went
        on telling the store to order less of something that never left the shelf.
        """
        credits = self.env["bf.retail.credit"].sudo().search(
            [("move_line_id", "in", self.line_ids.ids)])
        if credits:
            credits.unlink()

    def _bf_retail_write_credits(self):
        Credit = self.env["bf.retail.credit"].sudo()
        for move in self:
            lines = move.line_ids.filtered(
                lambda line: line.display_type == "product" and line.product_id
                and line.product_id.bf_retail_managed)
            if not lines:
                continue
            already = set(Credit.search([("move_line_id", "in", lines.ids)]).mapped(
                "move_line_id.id"))
            vals_list = []
            for line in lines:
                if line.id in already:
                    continue
                product = line.product_id
                qty = line._bf_retail_qty(product)
                if float_compare(qty, 0.0, precision_rounding=product.uom_id.rounding
                                 or 0.01) <= 0:
                    continue
                vals_list.append({
                    "product_id": product.id,
                    "company_id": move.company_id.id,
                    "partner_id": move.commercial_partner_id.id or move.partner_id.id,
                    "date": move.invoice_date or move.date,
                    "qty": qty,
                    "amount": abs(line.balance),
                    "move_line_id": line.id,
                })
            if vals_list:
                Credit.create(vals_list)
