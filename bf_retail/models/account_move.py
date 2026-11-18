# -*- coding: utf-8 -*-
from odoo import models

from .res_company import _bf_retail_dans_la_langue_du_commerce
from odoo.tools import float_compare, float_is_zero

# A purchase receipt is a purchase: the corner store that pays the wholesaler cash sees what
# the case cost like everyone else. Never a credit note (`in_refund`): a credit for returns
# lowers what the store really paid, and the lines of the bill itself say that (below).
BILLS = ("in_invoice", "in_receipt")


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        """A validated vendor bill writes the cost series by itself.

        Only a bill, never a credit note: a credit for returns (bread, milk, what did not
        sell) lowers what the store really paid, but it is not a new cost for the next unit.
        Folding credits into the cost is a lot 2 question, and pretending to do it here would
        flatter the margin.
        """
        posted = super()._post(soft=soft)
        bills = posted.filtered(lambda move: move.move_type in BILLS)
        if bills:
            bills._bf_retail_write_costs()
        return posted

    def button_draft(self):
        """🔴 A bill sent back to draft used to keep its cost: the series still said 12.00 and
        every margin and the suggested price were computed against it. A cost is what the store
        is about to pay for the next unit, and a bill that is not posted says nothing about
        that. Same shape as `_bf_retail_drop_credits` of lot 2."""
        res = super().button_draft()
        self._bf_retail_drop_costs()
        return res

    def button_cancel(self):
        """A cancelled bill, same thing: posted once is not paid for ever."""
        res = super().button_cancel()
        self._bf_retail_drop_costs()
        return res

    def _bf_retail_drop_costs(self):
        """Take the rows this bill wrote back out of the series.

        Through the ORM on purpose: `unlink` re-chains the series of each product, so the rows
        that followed stop reading their change against a cost that is gone.
        """
        rows = self.env["bf.retail.cost"].sudo().search([("move_id", "in", self.ids)])
        if rows:
            rows.unlink()

    def _bf_retail_write_costs(self):
        Cost = self.env["bf.retail.cost"].sudo()
        for move in self:
            company = move.company_id
            lines = move.line_ids.filtered(
                lambda line: line.display_type == "product" and line.product_id
                and line.product_id.bf_retail_managed)
            # The rows this bill wrote, read BY THE BILL and no longer line by line: one bill
            # gives ONE row per product, and that row carries the
            # first line of the product.
            rows = {}
            for row in Cost.search([("move_id", "=", move.id)], order="id asc"):
                kept = rows.setdefault(row.product_id.id, row)
                if kept != row:
                    # Two rows for one product of one bill is what this used to write, one per
                    # line: the extra ones leave rather than sit in the series for ever.
                    row.unlink()
            read, unreadable = move._bf_retail_per_product(lines)
            vals_list, touched, skipped = [], Cost.browse(), list(unreadable)
            for product, (qty, paid, first) in read.items():
                row = rows.pop(product.id, None)
                per_unit = product.uom_id.rounding or 0.01
                per_cent = company.currency_id.rounding or 0.01
                # What the bill leaves at zero or below is not a cost for the next unit: a
                # product billed then credited in full is not a purchase, and a rebate used to
                # become a POSITIVE cost, sourced "bill".
                if float_compare(qty, 0.0, precision_rounding=per_unit) <= 0 \
                        or float_compare(paid, 0.0, precision_rounding=per_cent) <= 0:
                    # Said out loud on the bill: a line of a followed product that writes no
                    # cost used to be dropped without a word, and the product looked as if the
                    # delivery had never been billed.
                    skipped.append(product.display_name)
                    if row:
                        row.unlink()
                    continue
                # `balance` is already in the company's currency and net of the discount and
                # of the taxes: no conversion of our own, and no rate to pick.
                vals = {
                    "product_id": product.id,
                    "company_id": company.id,
                    "date": move.invoice_date or move.date,
                    "cost": paid / qty,
                    "uom_id": product.uom_id.id,
                    "source": "bill",
                    "partner_id": move.commercial_partner_id.id or move.partner_id.id,
                    "move_line_id": first.id,
                }
                if not row:
                    vals_list.append(vals)
                    continue
                # 🔴 A bill set back to draft, corrected and posted again used to be SKIPPED
                # here, because its lines already had a row and the unique constraint forbade a
                # second one: the bill said 2.00, the series said 1.00, and every margin and
                # the whole index were computed against a cost the store never paid. The row of
                # a product is now rewritten FROM its lines. Since a bill that leaves the
                # posted state drops its rows, this is the second belt and no longer the first.
                changed = {field: value for field, value in vals.items()
                           if not row._bf_retail_same(field, value)}
                if changed:
                    row.write(changed)
                touched |= row
            # A row this bill wrote that no product of it claims any more (its product left the
            # retail, its lines were deleted on a correction, what is left of it is a credit):
            # its row leaves the series with them, rather than standing as a cost nothing says.
            for row in rows.values():
                row.unlink()
            if vals_list:
                touched |= Cost.create(vals_list)
            # The two bodies below are written in the language of the STORE, and not in the
            # one of the context: nothing carries a language when a cron validates the bill.
            dit = _bf_retail_dans_la_langue_du_commerce(move, company)
            if skipped:
                dit._message_log(body=dit.env._(
                    "No retail cost read for %s: the line costs nothing, or its quantity does "
                    "not convert to the unit of the product.", ", ".join(skipped[:10])))
            if touched:
                moved = touched.filtered(
                    lambda row: row.has_previous and abs(row.delta_pct) >= 0.005)
                if moved:
                    dit._message_log(body=dit.env._(
                        "Retail cost moved on %s of the %s product(s) of this bill.",
                        len(moved), len(touched)))

    def _bf_retail_per_product(self, lines):
        """What this bill really says, per product: units received, amount paid, first line.

        🔴 Two lines of the same product used to write two rows of the series on the same day,
        a phantom rise of +100 %, and a current cost that was the last row BY ID. And a rebate
        carried as a negative line on the same product was dropped, leaving the GROSS cost
        standing while the chatter announced that no cost had been read: 24 at 1.29 less 5.00
        is 1.0817 the unit, not 1.2900 -- 30 cents of margin on a bag of chips (
        arbitrage).

        A line whose amount is negative while its quantity is positive is a credit on goods
        already counted (a rebate, an allowance), so only its amount is taken. A line whose
        quantity is negative is a return, and both its quantity and its amount go.
        """
        self.ensure_one()
        per_cent = self.company_id.currency_id.rounding or 0.01
        read, unreadable = {}, []
        for line in lines:
            product = line.product_id
            qty = line._bf_retail_qty(product)
            credit = float_compare(line.balance, 0.0, precision_rounding=per_cent) < 0
            if not credit and float_is_zero(
                    qty, precision_rounding=product.uom_id.rounding or 0.01):
                # A unit that does not measure the same thing as the product's, or nothing
                # billed: this line cannot be folded into an amount per unit, and the bill
                # names its product rather than quietly spreading the amount over the others.
                unreadable.append(product.display_name)
                continue
            seen = read.setdefault(product, [0.0, 0.0, line])
            if not (credit and qty > 0.0):
                seen[0] += qty
            seen[1] += line.balance
        return read, unreadable


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def unlink(self):
        """A bill line that goes away takes its cost row with it, and the series is re-chained.

        🔴 The row used to survive its line, `move_line_id` set to NULL: the clean-up of the
        bill looked for the rows of the lines it no longer carried, and a DELETED line is in no
        list at all -- the series kept a cost of 12.00 that the corrected bill had dropped. The
        link is `ondelete="cascade"` now, which Postgres honours without passing through the
        ORM, so the keys are read BEFORE the delete and each series is rewritten after it.
        """
        Cost = self.env["bf.retail.cost"].sudo()
        keys = Cost.search([("move_line_id", "in", self.ids)])._bf_keys()
        res = super().unlink()
        if keys:
            Cost._bf_rechain(keys)
        return res

    def _bf_retail_qty(self, product):
        """The quantity of this line in the product's own unit of measure, 0 when the two
        units do not measure the same thing (a case bought, a kilogram stocked)."""
        self.ensure_one()
        line_uom = self.product_uom_id or product.uom_id
        if not line_uom or line_uom == product.uom_id:
            return self.quantity
        if line_uom.category_id != product.uom_id.category_id:
            return 0.0
        return line_uom._compute_quantity(self.quantity, product.uom_id, round=False)
