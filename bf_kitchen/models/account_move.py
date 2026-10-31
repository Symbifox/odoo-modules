import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero

_logger = logging.getLogger(__name__)

MANAGER = "bf_kitchen.group_kitchen_manager"
BILLING = "account.group_account_invoice"
# The fields of a bill line that the stock brought in by "Received" depends on.
RECEIPT_FIELDS = {"quantity", "product_id", "product_uom_id", "bf_ingredient_id", "bf_pack_qty"}
# Kitchen fields of a bill line written only as superuser, by the module's own actions after
# their checks (Received, Attach, Not an ingredient, the cost engine): the views show them read
# only, and the server must not take them from anyone else either.
SU_ONLY_LINE_FIELDS = ("bf_receipt_move_id", "bf_cost_issue", "bf_ingredient_id", "bf_pack_qty",
                       "bf_kitchen_ignore")


class AccountMove(models.Model):
    _inherit = "account.move"

    bf_kitchen_can_receive = fields.Boolean("Can be received", compute="_compute_bf_kitchen_can_receive")

    def _bf_kitchen_receivable_lines(self):
        self.ensure_one()
        return self.invoice_line_ids.filtered(
            lambda l: l.display_type == "product" and l.quantity > 0
            and l.product_id.product_tmpl_id.bf_kitchen_type == "ingredient"
            and l.product_id.is_storable
            and not l.purchase_line_id and not l._bf_kitchen_is_received())

    @api.depends("state", "move_type", "invoice_line_ids.product_id", "invoice_line_ids.bf_ingredient_id",
                 "invoice_line_ids.bf_receipt_move_id")
    def _compute_bf_kitchen_can_receive(self):
        for move in self:
            move.bf_kitchen_can_receive = (
                move.state == "draft" and move.move_type == "in_invoice"
                and move.company_id.bf_kitchen_receive_without_po
                and bool(move._bf_kitchen_receivable_lines()))

    # ── kitchen cost hooks ─────────────────────────────────────────────────────────────

    def _bf_kitchen_bills(self):
        return self.filtered(lambda m: m.move_type == "in_invoice")

    def _bf_kitchen_cost_bills(self):
        """Vendor bills whose lines may give a kitchen cost and that these moves touch: the
        bills themselves, and the bill a vendor credit note reverses."""
        refunds = self.filtered(lambda m: m.move_type == "in_refund")
        return self._bf_kitchen_bills() | refunds.reversed_entry_id._bf_kitchen_bills()

    def _bf_kitchen_is_refunded(self):
        """True when posted credit notes reverse the whole bill (with or without reconciliation):
        its lines stop giving a price. A partial credit note leaves the bill's price as it is."""
        self.ensure_one()
        if self.payment_state == "reversed":
            return True
        refunds = self.reversal_move_ids.filtered(lambda r: r.state == "posted" and r.move_type == "in_refund")
        if not refunds:
            return False
        currency = self.company_id.currency_id
        refunded = sum(abs(r.amount_total_signed) for r in refunds)
        return currency.compare_amounts(refunded, abs(self.amount_total_signed)) >= 0

    def _bf_kitchen_products(self):
        return self.line_ids.bf_kitchen_product_id.filtered(
            lambda p: p.product_tmpl_id.bf_kitchen_type == "ingredient")

    def _bf_kitchen_touched_products(self):
        products_by_company = {}
        for move in self._bf_kitchen_cost_bills():
            products_by_company.setdefault(move.company_id, self.env["product.product"])
            products_by_company[move.company_id] |= move._bf_kitchen_products()
        return products_by_company

    def _bf_kitchen_after_change(self, products_by_company=None):
        """Refresh the kitchen costs touched by these moves. Never raises.

        Everything runs in savepoints, the search of the products touched included: a kitchen
        cost must never block a validation, and an ``except`` alone would not catch an SQL
        error left in the transaction.
        """
        bills = self.env["account.move"]
        try:
            with self.env.cr.savepoint():
                bills = self._bf_kitchen_cost_bills()
                if products_by_company is None:
                    products_by_company = self._bf_kitchen_touched_products()
        except Exception:
            _logger.exception("Kitchen cost refresh failed for moves %s", self.ids)
            return
        for company, products in products_by_company.items():
            if not products:
                continue
            try:
                with self.env.cr.savepoint():
                    lines = bills.filtered(lambda m: m.company_id == company).line_ids
                    lines._bf_kitchen_flag_cost_issue(company)
                    dishes = products.sudo()._bf_kitchen_refresh_costs(company)
                    self._bf_kitchen_alert(company, dishes, products)
            except Exception:
                _logger.exception("Kitchen cost refresh failed for moves %s", self.ids)

    def _bf_kitchen_alert(self, company, dishes, causes):
        """One activity per dish pushed over its target (or further over it) by these bills.

        The note carries no figure: the activity is readable by anyone who reads the product,
        the cook included, and the costs are for the kitchen manager. The figures are on the
        recipe.
        """
        activity_type = self.env.ref("bf_kitchen.mail_activity_plate_cost", raise_if_not_found=False)
        if not activity_type or not dishes:
            return
        user = company.bf_kitchen_manager_id or self.env.user
        Activity = self.env["mail.activity"].sudo()
        rising = [d for d, (old, new) in dishes.items() if d.lst_price and new > old]
        if not rising:
            return
        Product = self.env["product.product"]
        boms = self.env["mrp.bom"].sudo()._bom_find(Product.concat(*rising), company_id=company.id)
        for dish in rising:
            old_cost, new_cost = dishes[dish]
            price = dish.lst_price
            bom = boms.get(dish)
            if not bom:
                continue
            target = bom.bf_target_pct or company.bf_kitchen_target_pct
            old_pct, new_pct = old_cost / price * 100.0, new_cost / price * 100.0
            if new_pct <= target:
                continue
            if old_pct > target and new_pct - old_pct < company.bf_kitchen_alert_points:
                continue
            tmpl = dish.product_tmpl_id
            exists = Activity.search_count([
                ("res_model", "=", "product.template"), ("res_id", "=", tmpl.id),
                ("activity_type_id", "=", activity_type.id)])
            if exists:
                continue
            used = causes.filtered(lambda p: p in bom.bom_line_ids.product_id) or causes
            # Written in the language of the person who receives it, not of the one who
            # validated the bill.
            env = self.sudo().with_context(lang=user.lang or self.env.lang).env
            note = env._("The plate cost went over its target after %(bills)s. Ingredients: %(ingredients)s. "
                         "The figures are on the recipe.",
                         bills=", ".join(self.with_env(env).mapped("display_name")),
                         ingredients=", ".join(used[:5].with_env(env).mapped("display_name")))
            tmpl.sudo().activity_schedule(
                activity_type_id=activity_type.id, user_id=user.id,
                summary=env._("Plate cost over target: %s", tmpl.with_env(env).display_name), note=note)

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        # A vendor credit note refreshes the bill it reverses (cancel or standard reversal).
        posted._bf_kitchen_after_change()
        return posted

    def button_draft(self):
        moves = self.filtered(lambda m: m.state == "posted" and m.move_type in ("in_invoice", "in_refund"))
        res = super().button_draft()
        moves._bf_kitchen_after_change()
        return res

    def button_cancel(self):
        moves = self.filtered(lambda m: m.move_type in ("in_invoice", "in_refund"))
        # Every move with received lines, whatever its type now (a draft bill can be switched
        # into a credit note).
        self._bf_kitchen_return_receipts()
        res = super().button_cancel()
        moves._bf_kitchen_after_change()
        return res

    def unlink(self):
        self.line_ids._bf_kitchen_check_not_received()
        return super().unlink()

    # ── "Received": ingredients into stock from a draft vendor bill ──────────────────────

    def action_bf_kitchen_receive(self):
        """Bring the ingredient lines of this draft bill into stock, one receipt move per line.

        Only lines with a product: a line without one is booked to an expense account, and its
        receipt would leave the stock interim account unbalanced in perpetual valuation. On a
        draft bill, the product is simply set on the line first.
        """
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("Only a kitchen manager can receive ingredients from a bill."))
        self.check_access("write")
        if self.state != "draft" or self.move_type != "in_invoice":
            raise UserError(_("Ingredients are received from a draft vendor bill."))
        lines = self._bf_kitchen_receivable_lines()
        if not lines:
            raise UserError(_("Nothing to receive: every ingredient line is already received, comes "
                              "from a purchase order, or has no product (set the ingredient as the "
                              "product of the line first)."))
        company = self.company_id
        warehouse = company._bf_kitchen_warehouse()
        if not warehouse:
            raise UserError(_("The company %s has no warehouse.", company.name))
        ptype = warehouse.in_type_id
        src = ptype.default_location_src_id or self.partner_id.property_stock_supplier
        dest = ptype.default_location_dest_id
        moves = []
        for line in lines:
            product = line.product_id
            qty, issue = line._bf_kitchen_stock_qty(product, company)
            if issue:
                raise UserError(issue)
            unit_cost = abs(line.balance) / qty if qty else 0.0
            moves.append((line, {
                "name": line.name or product.display_name,
                "product_id": product.id,
                "product_uom_qty": qty,
                "product_uom": product.uom_id.id,
                "location_id": src.id,
                "location_dest_id": dest.id,
                "company_id": company.id,
                "price_unit": unit_cost,
                "picking_type_id": ptype.id,
                # The marker the cancellation and the valuation check.
                "bf_kitchen_bill_line_id": line.id,
            }))
        picking = self.env["stock.picking"].sudo().create({
            "partner_id": self.partner_id.id,
            "picking_type_id": ptype.id,
            "location_id": src.id,
            "location_dest_id": dest.id,
            "origin": self.ref or self.name or _("Vendor bill"),
            "company_id": company.id,
            "move_ids": [(0, 0, vals) for _line, vals in moves],
        })
        picking.action_confirm()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
            move.bf_kitchen_bill_line_id.bf_receipt_move_id = move.id
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        self._message_log(body=_("Ingredients received into stock: %s.", picking._get_html_link()))
        return True

    def _bf_kitchen_return_receipts(self):
        """A cancelled bill sends back what its Received button brought in and is still in stock.

        Only what is still in is returned: a return already made from Inventory is deducted, and
        what the kitchen already used (sold, scrapped, counted out) cannot go back to the vendor,
        so the return stops at what is available in the receipt's location. The rest is noted
        on the bill, for the person who settles it with the vendor. Only a receipt made by the
        Received button of this very line, for its product, is ever returned.
        If the return fails, the cancellation is refused with the reason: a cancelled bill whose
        goods silently stay in stock is the error this button exists to avoid.
        """
        Quant = self.env["stock.quant"].sudo()
        for move in self:
            todo, short, available = {}, [], {}
            for line in move.invoice_line_ids:
                rmove = line._bf_kitchen_receipt_move()
                qty = rmove._bf_kitchen_net_qty() if rmove else 0.0
                rounding = rmove.product_id.uom_id.rounding or 0.01
                if float_compare(qty, 0.0, precision_rounding=rounding) <= 0:
                    continue
                key = (rmove.product_id, rmove.location_dest_id)
                if key not in available:
                    available[key] = max(Quant._get_available_quantity(*key), 0.0)
                take = min(qty, available[key])
                available[key] -= take
                if float_compare(take, qty, precision_rounding=rounding) < 0:
                    short.append((rmove.product_id, qty - take))
                if float_compare(take, 0.0, precision_rounding=rounding) > 0:
                    todo.setdefault(rmove.picking_id, {})[rmove] = take
            if short:
                move.sudo()._message_log(body=_(
                    "Cancelled with ingredients already used, so not sent back to the vendor: %s.",
                    ", ".join("%s %s %s" % (round(q, 3), p.uom_id.name, p.display_name) for p, q in short)))
            if not todo:
                continue
            try:
                with self.env.cr.savepoint():
                    for picking, qtys in todo.items():
                        wizard = self.env["stock.return.picking"].sudo().with_context(
                            active_id=picking.id, active_model="stock.picking").create({"picking_id": picking.id})
                        for rline in wizard.product_return_moves:
                            rline.quantity = qtys.get(rline.move_id, 0.0)
                        ret = wizard._create_return()
                        for smove in ret.move_ids:
                            smove.quantity = smove.product_uom_qty
                            smove.picked = True
                        ret.with_context(skip_backorder=True, skip_sms=True).button_validate()
            except Exception as error:
                raise UserError(_(
                    "%(bill)s: the ingredients it brought into stock could not be sent back (%(error)s). "
                    "Return the receipt from Inventory, then cancel the bill.",
                    bill=move.display_name, error=error)) from error


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    bf_ingredient_id = fields.Many2one(
        "product.product", "Kitchen ingredient", index="btree_not_null", copy=False,
        domain="[('product_tmpl_id.bf_kitchen_type', '=', 'ingredient')]",
        help="Ingredient this line was attached to, when it has no product. The product of a "
             "posted line is never rewritten: Odoo would recompute its label, unit, price, taxes "
             "and analytic distribution.")
    bf_pack_qty = fields.Float(
        "Stock units per billed unit", digits="Product Unit of Measure", copy=False,
        help="Format of the billed unit, in the ingredient's stock unit.")
    bf_kitchen_product_id = fields.Many2one(
        "product.product", compute="_compute_bf_kitchen_product_id", string="Kitchen product")
    bf_cost_issue = fields.Char("Cost issue", copy=False, readonly=True)
    bf_kitchen_ignore = fields.Boolean("Not an ingredient", copy=False)
    bf_receipt_move_id = fields.Many2one("stock.move", "Kitchen receipt", copy=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # After create: a default_* key of the context fills these fields too.
        if not self.env.su and any(line[f] for line in lines for f in SU_ONLY_LINE_FIELDS):
            lines._bf_kitchen_refuse_su_fields()
        return lines

    def _bf_kitchen_refuse_su_fields(self):
        raise AccessError(_("The kitchen fields of a bill line (ingredient, format, receipt, cost issue, "
                            "not an ingredient) are set by the kitchen's own buttons only."))

    @api.depends("product_id", "bf_ingredient_id")
    def _compute_bf_kitchen_product_id(self):
        for line in self:
            line.bf_kitchen_product_id = line.product_id or line.bf_ingredient_id

    # ── stock brought in by "Received" ────────────────────────────────────────────────

    def _bf_kitchen_receipt_move(self):
        """The receipt made by this line's Received button, as superuser, or an empty recordset:
        only a move marked as received from this very line, for its product, counts."""
        self.ensure_one()
        move = self.sudo().bf_receipt_move_id
        if move and move.bf_kitchen_bill_line_id.id == self.id and move.product_id == self.bf_kitchen_product_id:
            return move
        return self.env["stock.move"].sudo()

    def _bf_kitchen_received_qty(self):
        """What this line's Received button brought in and is still in stock (receipt minus
        returns), in the product's stock unit."""
        self.ensure_one()
        move = self._bf_kitchen_receipt_move()
        return move._bf_kitchen_net_qty() if move else 0.0

    def _bf_kitchen_is_received(self):
        self.ensure_one()
        move = self._bf_kitchen_receipt_move()
        if not move:
            return False
        return not float_is_zero(self._bf_kitchen_received_qty(), precision_rounding=move.product_id.uom_id.rounding)

    def _bf_kitchen_check_not_received(self):
        received = self.filtered(lambda l: l.bf_receipt_move_id and l._bf_kitchen_is_received())
        if received:
            raise UserError(_(
                "%(lines)s: already received into stock. Cancel the bill to send the ingredients "
                "back, or return the receipt from Inventory, before deleting it.",
                lines=", ".join(received[:3].mapped(lambda l: l.name or l.move_id.display_name))))

    def _bf_kitchen_receipt_key(self):
        """(product, quantity in stock unit) the receipt of this line stands for."""
        product = self.bf_kitchen_product_id
        qty, _issue = self._bf_kitchen_stock_qty(product, self.company_id) if product else (0.0, False)
        return product, qty

    def write(self, vals):
        if not self.env.su and set(SU_ONLY_LINE_FIELDS) & set(vals):
            self._bf_kitchen_refuse_su_fields()
        received = {}
        # No context key lifts this: the receipt itself only writes bf_receipt_move_id.
        if RECEIPT_FIELDS & set(vals):
            for line in self.sudo().filtered("bf_receipt_move_id"):
                if line._bf_kitchen_is_received():
                    received[line] = line._bf_kitchen_receipt_key()
        res = super().write(vals)
        for line, (product, qty) in received.items():
            new_product, new_qty = line._bf_kitchen_receipt_key()
            rounding = (product.uom_id.rounding or 0.01)
            if new_product != product or float_compare(new_qty, qty, precision_rounding=rounding):
                raise UserError(_(
                    "%(line)s: already received into stock (%(qty)s %(unit)s of %(product)s), so its "
                    "product, unit and quantity can no longer change. Cancel the bill to send the "
                    "ingredients back, or return the receipt from Inventory, then change the line.",
                    line=line.name or line.move_id.display_name, qty=round(qty, 3),
                    unit=product.uom_id.name, product=product.display_name))
        return res

    def unlink(self):
        self._bf_kitchen_check_not_received()
        return super().unlink()

    # ── kitchen cost of a line ─────────────────────────────────────────────────────────

    def _bf_kitchen_stock_qty(self, product, company):
        """Quantity of this line in the product's stock unit, or (0, reason).

        The format (stock units per billed unit) is the line's own, set when it was attached,
        or the vendor's, kept on the vendor line of the product **for one billed unit**: it
        applies only to the lines in that unit. A line of the same vendor in another unit is
        converted as usual (a case of 12 x 1 L must not turn 12000 g into 144 million g).
        """
        self.ensure_one()
        pack = self.bf_pack_qty
        if not pack:
            info = product.sudo().seller_ids.filtered(
                lambda s: s.partner_id == self.partner_id.commercial_partner_id
                and s.company_id in (company, self.env["res.company"])
                and (not s.product_id or s.product_id == product) and s.bf_pack_qty > 0
                and s.bf_pack_uom_id == self.product_uom_id)[:1]
            pack = info.bf_pack_qty
        if pack:
            return self.quantity * pack, False
        if self.product_uom_id and self.product_uom_id != product.uom_id:
            if self.product_uom_id.category_id != product.uom_id.category_id:
                return 0.0, _("%(product)s: the bill unit (%(unit)s) cannot be converted to the stock "
                              "unit (%(stock)s). Give the format on the line first.",
                              product=product.display_name, unit=self.product_uom_id.name,
                              stock=product.uom_id.name)
            return self.product_uom_id._compute_quantity(self.quantity, product.uom_id, round=False), False
        return self.quantity, False

    def _bf_kitchen_flag_cost_issue(self, company):
        """Flag (or clear) the kitchen ingredient lines of posted vendor bills that cannot give
        a cost, so they land in Costs to check even when an older line is not read again."""
        for line in self.sudo():
            product = line.bf_kitchen_product_id
            if (line.display_type != "product" or line.parent_state != "posted"
                    or line.move_id.move_type != "in_invoice"
                    or product.product_tmpl_id.bf_kitchen_type != "ingredient"):
                continue
            _cost, issue = product._bf_kitchen_line_unit_cost(line, company)
            if (line.bf_cost_issue or False) != (issue or False):
                line.bf_cost_issue = issue or False

    # ── lines to attach ────────────────────────────────────────────────────────────────

    def action_bf_kitchen_attach(self):
        self.ensure_one()
        self._bf_kitchen_check_attach_rights()
        return {
            "type": "ir.actions.act_window",
            "name": _("Attach to an ingredient"),
            "res_model": "bf.kitchen.attach.line",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_bf_kitchen_ignore(self):
        self._bf_kitchen_check_attach_rights()
        if any(l.move_id.move_type != "in_invoice" or l.display_type != "product" or l.product_id for l in self):
            raise UserError(_("Only the vendor bill lines without a product can be set aside as not an ingredient."))
        self.sudo().bf_kitchen_ignore = True

    def _bf_kitchen_check_attach_rights(self):
        """Attaching or ignoring a bill line takes a kitchen manager who also reads the bills
        (Accounting / Billing); the line itself must be readable by that person."""
        user = self.env.user
        if not self.env.su and not (user.has_group(MANAGER) and user.has_group(BILLING)):
            raise AccessError(_("Attaching bill lines to ingredients takes the Kitchen manager and "
                                "Accounting / Billing rights."))
        # The kitchen fields are then written as superuser: the line must be one this person
        # could write.
        self.check_access("write")
