# -*- coding: utf-8 -*-
import math
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

from odoo.addons.bf_retail.models.res_company import _bf_retail_dans_la_langue_du_commerce

MANAGER = "bf_retail.group_retail_manager"
BUYER = "purchase.group_purchase_user"

# What came back over this many days is shown next to the quantity to order: three months
# covers a seasonal magazine and a bread line that never sells on Mondays.
CREDIT_WINDOW = 90


class RetailOrderGuide(models.Model):
    """The order of one supplier, written over a day and kept.

    A corner store does not order in one sitting: the owner walks the aisle, writes what is
    missing, the rep calls, the van comes on Thursday. So the guide is a record, not a
    wizard: it survives the session, keeps what was typed, and refreshes the facts around
    it. That is the whole difference with the kitchen's order guide, which is transient.
    """
    _name = "bf.retail.order.guide"
    _description = "Retail order guide"
    _order = "date desc, id desc"
    _inherit = ["mail.thread"]
    _check_company_auto = True

    name = fields.Char("Reference", required=True, copy=False, readonly=True, default="/")
    company_id = fields.Many2one(
        "res.company", "Company", required=True, readonly=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one(
        "res.partner", "Supplier", required=True, check_company=True, tracking=True,
        domain="[('bf_retail_supplier', '=', True)]")
    warehouse_id = fields.Many2one(
        "stock.warehouse", "Store", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1))
    date = fields.Date("Written on", required=True, default=fields.Date.context_today)
    delivery_date = fields.Date(
        "For the delivery of", readonly=True,
        help="The first delivery this order can still make, from the supplier's delivery "
             "days, its cut-off hour and its lead time.")
    cover_until = fields.Date(
        "Covering until", readonly=True,
        help="The delivery after the next one: the quantities cover until then, so the shelf "
             "does not empty the day after a delivery.")
    state = fields.Selection(
        [("draft", "Draft"), ("computed", "Filled"), ("ordered", "Ordered"),
         ("closed", "Closed")],
        default="draft", required=True, readonly=True, tracking=True)
    order_id = fields.Many2one("purchase.order", "Request for quotation", readonly=True)
    line_ids = fields.One2many("bf.retail.order.guide.line", "guide_id", "Items")
    line_count = fields.Integer("Items", compute="_compute_totals")
    to_order_count = fields.Integer("Items to order", compute="_compute_totals")
    amount_total = fields.Monetary(
        "Total", currency_field="currency_id", compute="_compute_totals")
    below_minimum = fields.Boolean("Below the minimum", compute="_compute_totals")
    price_missing_count = fields.Integer(
        "Items without a price", compute="_compute_totals",
        help="Items to order whose supplier has no price in force today: an expired vendor "
             "line is not a price of 0.00, and the total below does not include them.")
    minimum = fields.Monetary(
        "Minimum order", currency_field="currency_id",
        related="partner_id.bf_retail_min_order")
    note = fields.Text("Note")

    @api.depends("line_ids.qty", "line_ids.amount", "line_ids.price_missing",
                 "partner_id.bf_retail_min_order")
    def _compute_totals(self):
        for guide in self:
            lines = guide.line_ids
            to_order = lines.filtered(lambda line: line.qty > 0)
            total = sum(to_order.mapped("amount"))
            guide.line_count = len(lines)
            guide.to_order_count = len(to_order)
            guide.amount_total = total
            guide.price_missing_count = len(
                to_order.filtered(lambda line: line.price_missing))
            minimum = guide.partner_id.bf_retail_min_order or 0.0
            guide.below_minimum = bool(to_order) and minimum > 0.0 and total < minimum

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(
                    vals.get("company_id") or self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(company).next_by_code(
                    "bf.retail.order.guide") or "/"
        return super().create(vals_list)

    def _bf_check_manager(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("An order guide is the retail manager's."))

    def _bf_open(self):
        for guide in self:
            if guide.state == "closed":
                raise UserError(_("%s is closed: write a new guide.", guide.name))
            if guide.state not in ("draft", "computed"):
                raise UserError(_("%s is already ordered.", guide.name))

    def _bf_tz(self):
        """The one resolution of the store's time zone, shared with the supplier.

        Two resolutions of the same question used to live side by side, and they disagreed
        as soon as a store carried its own address.
        """
        self.ensure_one()
        return self.partner_id._bf_retail_tz(self.warehouse_id)

    def _bf_today(self):
        return datetime.now(pytz.utc).astimezone(self._bf_tz()).date()

    def _bf_local_end(self, day):
        tz = self._bf_tz()
        return tz.localize(datetime.combine(day, time(23, 59, 59))).astimezone(
            pytz.utc).replace(tzinfo=None)

    def _bf_sellers(self, product, today):
        """The vendor lines of this product this company can use today, preferred first."""
        company = self.company_id
        return product._prepare_sellers().filtered(
            lambda s: s.company_id in (company, self.env["res.company"])
            and (not s.product_id or s.product_id == product)
            and (not s.date_start or s.date_start <= today)
            and (not s.date_end or s.date_end >= today))

    def _bf_items(self, today):
        """[(product, reordering rule)] this supplier is the one to order from.

        The supplier of the item's rule in this store wins; without one, the preferred vendor
        of the product. An item sold by two suppliers lands in ONE guide, never two, so two
        guides of the same day cannot order it twice.
        """
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id.commercial_partner_id
        Info = self.env["product.supplierinfo"].sudo()
        infos = Info.search([("partner_id", "child_of", partner.id),
                             ("company_id", "in", (company.id, False))])
        Rule = self.env["stock.warehouse.orderpoint"].sudo()
        rules = Rule.search([("warehouse_id", "=", self.warehouse_id.id),
                             ("company_id", "=", company.id),
                             ("product_id.bf_retail_managed", "=", True)])
        candidates = rules.product_id
        for info in infos:
            candidates |= info.product_id or info.product_tmpl_id.product_variant_ids
        root = self.warehouse_id.lot_stock_id
        items = []
        for product in candidates.sudo().with_company(company):
            if not product.bf_retail_managed or not product.is_storable \
                    or product.company_id not in (company, self.env["res.company"]):
                continue
            mine = rules.filtered(lambda r: r.product_id == product)
            rule = mine.filtered(lambda r: r.location_id == root)[:1] or mine[:1]
            if rule.supplier_id:
                preferred = rule.supplier_id.partner_id.commercial_partner_id
            else:
                preferred = self._bf_sellers(product, today)[:1].partner_id.commercial_partner_id
            if preferred == partner:
                items.append((product, rule))
        return items

    def _bf_credits(self, products, today):
        """{product: quantity credited over the window}, to order less of what comes back."""
        if not products:
            return {}
        rows = self.env["bf.retail.credit"].sudo()._read_group(
            [("product_id", "in", products.ids), ("company_id", "=", self.company_id.id),
             ("date", ">=", today - timedelta(days=CREDIT_WINDOW))],
            groupby=["product_id"], aggregates=["qty:sum"])
        return {product: qty for product, qty in rows}

    def action_compute(self):
        """Fill the guide, keeping every quantity a person typed.

        A line whose quantity differs from the quantity suggested was decided by someone:
        the facts around it are refreshed, the quantity is not touched. That is what makes a
        guide worth keeping open while walking the aisle.
        """
        self.ensure_one()
        self._bf_check_manager()
        self._bf_open()
        today = self._bf_today()
        delivery, after = self.partner_id._bf_retail_deliveries(
            warehouse=self.warehouse_id)
        items = self._bf_items(today)
        warehouse = self.warehouse_id
        until = self._bf_local_end(after)
        products = self.env["product.product"].browse([p.id for p, _r in items])
        # Requests for quotation not confirmed yet: Odoo's forecast does not see them.
        _by_location, in_progress = products.sudo()._get_quantity_in_progress(
            warehouse_ids=warehouse.ids)
        credits = self._bf_credits(products, today)
        Cost = self.env["bf.retail.cost"]
        existing = {line.product_id.id: line for line in self.line_ids}
        written = []
        for product, rule in items:
            forecast = product.with_context(
                warehouse_id=warehouse.id, to_date=until).virtual_available
            pending = in_progress.get((product.id, warehouse.id), 0.0)
            free = forecast + pending
            rounding = product.uom_id.rounding or 0.01
            if rule:
                low, high = rule.product_min_qty, rule.product_max_qty
                need = high - free \
                    if float_compare(free, low, precision_rounding=rounding) < 0 else 0.0
                if need > 0 and rule.qty_multiple > 0:
                    need = math.ceil(need / rule.qty_multiple - 1e-9) * rule.qty_multiple
            else:
                low = high = 0.0
                need = -free if float_compare(free, 0.0, precision_rounding=rounding) < 0 else 0.0
            sellers = self._bf_sellers(product, today).filtered(
                lambda s: s.partner_id.commercial_partner_id
                == self.partner_id.commercial_partner_id)
            uom = sellers[:1].product_uom or product.uom_po_id
            if uom.category_id != product.uom_id.category_id:
                uom = product.uom_id
            suggested = 0.0
            if float_compare(need, 0.0, precision_rounding=rounding) > 0:
                suggested = math.ceil(
                    product.uom_id._compute_quantity(need, uom, round=False) - 1e-9)
                # The smallest tier's minimum: a bigger tier is cheaper, not required.
                suggested = max(suggested, min(sellers.mapped("min_qty") or [0.0]))
            info = product._select_seller(
                partner_id=self.partner_id, quantity=suggested or None, date=today, uom_id=uom)
            # 🔴 "Today" is the STORE's, never the user's. The reading of the series
            # stops at today, and its own default reads the user's time zone: a store in
            # Auckland read by somebody in Toronto was shown yesterday's cost. ONE
            # resolution for ONE decision, which is this module's own rule.
            row = Cost._bf_last(product, self.company_id, upto=today)
            facts = {
                "product_id": product.id, "uom_id": uom.id,
                "stock_uom_id": product.uom_id.id,
                "on_hand": product.with_context(warehouse_id=warehouse.id).qty_available,
                "forecast": forecast, "in_progress": pending,
                "min_qty": low, "max_qty": high, "suggested": suggested,
                "price_unit": info.price if info else 0.0,
                "supplierinfo_id": info.id if info else False,
                # 🔴 Absent is not zero. An item whose only vendor line expired came out at
                # price 0.00, which read as free: the line counted for nothing in the total,
                # the guide called the order below the minimum against a total it knew was
                # short, and the request for quotation left for the supplier at 0.00.
                "price_missing": not info,
                "cost_last": row.cost, "cost_delta_pct": row.delta_pct if row.has_previous else 0.0,
                "cost_date": row.date, "credited_qty": credits.get(product, 0.0),
            }
            line = existing.pop(product.id, None)
            if line:
                if not line.edited:
                    facts["qty"] = suggested
                    facts["edited"] = False
                line.write(facts)
            else:
                facts["qty"] = suggested
                written.append(fields.Command.create(facts))
        # What the supplier no longer sells, or what no longer needs ordering: dropped unless
        # someone typed a quantity on it.
        for line in existing.values():
            if not line.edited:
                written.append(fields.Command.delete(line.id))
        self.write({"line_ids": written, "delivery_date": delivery, "cover_until": after,
                    "state": "computed"})
        return True

    def action_create_rfq(self):
        """Odoo's own request for quotation, in the item's purchase unit, for the delivery day.

        Made with the user's own purchase rights: a store owner who is not a buyer in Odoo
        is told so rather than having an order created in their name by a sudo.
        """
        self.ensure_one()
        self._bf_check_manager()
        self._bf_open()
        if not self.env.user.has_group(BUYER):
            raise AccessError(_("Creating a request for quotation takes the Purchase rights: "
                                "ask purchasing, or give the retail manager the Purchase "
                                "user group."))
        lines = self.line_ids.filtered(lambda line: float_compare(
            line.qty, 0.0, precision_digits=4) > 0)
        if not lines:
            raise UserError(_("Nothing to order: every quantity is 0."))
        # A price of 0.00 does not leave for the supplier. The guide already says which
        # items have no price in force; an order cannot be written on them.
        muettes = lines.filtered(lambda line: line.price_missing)
        if muettes:
            raise UserError(_(
                "No price in force for %(articles)s: this supplier's vendor line is expired "
                "or missing. Read a price file, or write the price on the vendor line, "
                "before asking for a quotation — an order does not leave at 0.00.",
                articles=", ".join(muettes.mapped("product_id.display_name"))))
        day = self.delivery_date or self._bf_today()
        planned = self.partner_id._bf_retail_local_noon(day, self.warehouse_id)
        order = self.env["purchase.order"].with_company(self.company_id).create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "picking_type_id": self.warehouse_id.in_type_id.id,
            "date_planned": planned,
            "origin": self.name,
            "order_line": [fields.Command.create({
                "product_id": line.product_id.id,
                "product_qty": line.qty,
                "product_uom": line.uom_id.id,
                "date_planned": planned,
            }) for line in lines],
        })
        self.write({"order_id": order.id, "state": "ordered"})
        dit = _bf_retail_dans_la_langue_du_commerce(self, self.company_id)
        # 🔴 `_message_log` and not `message_post`: a report of what the module just did has
        # nobody to notify, and `message_post` REQUIRES an address for its author -- a
        # manager with no email saw the order created and then the whole gesture rolled back
        # by the body of its own thread.
        dit._message_log(body=dit.env._("Request for quotation %s, %s item(s).",
                                        order.name, len(lines)))
        return {"type": "ir.actions.act_window", "res_model": "purchase.order",
                "res_id": order.id, "view_mode": "form", "target": "current"}

    def action_close(self):
        self._bf_check_manager()
        self.write({"state": "closed"})
        return True


class RetailOrderGuideLine(models.Model):
    _name = "bf.retail.order.guide.line"
    _description = "Retail order guide item"
    _order = "suggested desc, id"
    _check_company_auto = True

    guide_id = fields.Many2one(
        "bf.retail.order.guide", "Guide", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="guide_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="guide_id.currency_id")
    product_id = fields.Many2one(
        "product.product", "Item", required=True, ondelete="cascade", check_company=True)
    uom_id = fields.Many2one(
        "uom.uom", "Ordered in", required=True,
        help="The purchase unit of the item: the quantity to order is counted in it.")
    stock_uom_id = fields.Many2one(
        "uom.uom", "Stocked in", readonly=True,
        help="The item's own unit. What is on hand, what the forecast says and what came "
             "back are counted in it, and the quantity to order is not: a guide can read "
             "24 came back and 2 to order without contradicting itself.")
    on_hand = fields.Float(
        "On hand", digits="Product Unit of Measure", readonly=True,
        help="In the item's own unit, the one it is stocked in.")
    forecast = fields.Float(
        "Forecast", digits="Product Unit of Measure", readonly=True,
        help="What is left at the delivery after the next one, as Odoo forecasts it, in "
             "the item's own unit.")
    in_progress = fields.Float(
        "Already ordered", digits="Product Unit of Measure", readonly=True,
        help="On a request for quotation not confirmed yet: Odoo's forecast ignores it. "
             "In the item's own unit.")
    min_qty = fields.Float("Minimum", digits="Product Unit of Measure", readonly=True)
    max_qty = fields.Float("Maximum", digits="Product Unit of Measure", readonly=True)
    suggested = fields.Float("Suggested", digits="Product Unit of Measure", readonly=True)
    qty = fields.Float("To order", digits="Product Unit of Measure", default=0.0)
    edited = fields.Boolean(
        "Decided by hand", readonly=True, copy=False,
        help="Somebody typed this quantity: filling the guide again leaves it alone.")
    price_unit = fields.Monetary("Price", currency_field="currency_id", readonly=True)
    price_missing = fields.Boolean(
        "No price", readonly=True, copy=False,
        help="This supplier has no price in force for the item today: its vendor line is "
             "expired, or there is none. The price shown is not 0.00 because the item is "
             "free, and no request for quotation leaves on it.")
    amount = fields.Monetary(
        "Amount", currency_field="currency_id", compute="_compute_amount", store=True)
    supplierinfo_id = fields.Many2one("product.supplierinfo", "Vendor line", readonly=True)
    cost_last = fields.Float("Last cost", digits=(16, 4), readonly=True)
    cost_date = fields.Date("Cost of", readonly=True)
    cost_delta_pct = fields.Float(
        "Cost moved (%)", digits=(16, 2), readonly=True,
        help="Against the cost before it, from the cost series of the store.")
    credited_qty = fields.Float(
        "Came back", digits="Product Unit of Measure", readonly=True,
        help="Credited by the supplier over the last three months, in the item's own unit: "
             "a reason to order less.")

    _sql_constraints = [
        ("qty_positive", "CHECK (qty >= 0)", "A quantity cannot be negative."),
        ("one_product_per_guide", "UNIQUE (guide_id, product_id)",
         "This item is already in the guide."),
    ]

    def write(self, vals):
        """A quantity written by a person marks the line, once and for good.

        🔴 The mark used to be computed as `qty != suggested`. The day the suggestion
        caught up with the 20 somebody had decided — the shelf filled in between — the
        mark fell back to false, and the next fill wiped the 20 down to 0. A decision is
        an event, not a difference.
        """
        if "qty" in vals and "edited" not in vals:
            vals = dict(vals, edited=True)
        return super().write(vals)

    @api.depends("qty", "price_unit")
    def _compute_amount(self):
        for line in self:
            line.amount = line.qty * line.price_unit
