import math
from datetime import datetime, time, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero

from .kitchen_consumption import STOCK_TYPES

MANAGER = "bf_kitchen.group_kitchen_manager"
BUYER = "purchase.group_purchase_user"


class KitchenWeekday(models.Model):
    _name = "bf.kitchen.weekday"
    _description = "Day of the week"
    _order = "code"

    name = fields.Char(required=True, translate=True)
    code = fields.Integer(required=True, help="0 is Monday, 6 is Sunday.")

    _sql_constraints = [("one_per_day", "UNIQUE(code)", "One record per day of the week.")]


class ResPartner(models.Model):
    _inherit = "res.partner"

    bf_kitchen_delivery_day_ids = fields.Many2many(
        "bf.kitchen.weekday", "bf_kitchen_partner_delivery_day_rel", "partner_id", "weekday_id",
        "Delivery days", help="The days this supplier delivers. Empty: any day.")
    bf_kitchen_order_days = fields.Integer(
        "Order ahead (days)", default=1,
        help="How many days before a delivery the order must reach this supplier.")

    def _bf_kitchen_deliveries(self, today):
        """(next delivery the kitchen can still order for, the delivery after it): the order covers
        what the kitchen needs until the delivery after."""
        self.ensure_one()
        days = set(self.bf_kitchen_delivery_day_ids.mapped("code"))
        first = today + timedelta(days=max(self.bf_kitchen_order_days, 0))
        if not days:
            return first, first + timedelta(days=7)
        nxt = next(first + timedelta(days=i) for i in range(7) if (first + timedelta(days=i)).weekday() in days)
        after = next(nxt + timedelta(days=i) for i in range(1, 8) if (nxt + timedelta(days=i)).weekday() in days)
        return nxt, after


# Token the native clean-ups carry to spare the kitchen's stock levels; an object, which an RPC
# context (JSON) cannot carry.
KEEP_LEVELS = object()
# What the kitchen manager does not set on a rule: how it is replenished, and by whom.
RULE_PROTECTED = {"route_id", "group_id", "company_id", "trigger", "warehouse_id"}


class StockWarehouseOrderpoint(models.Model):
    """The kitchen manager sets the min and max of kitchen items, on Odoo's own reordering rules:
    the kitchen's access lines give the write, the scope keeps it to kitchen items, manual rules,
    in the stock of their warehouse. Ordering stays with Purchase: the native Order button is
    refused to a kitchen manager without the Purchase rights. (The native Replenish button of a
    product form stays Inventory's own right: a draft request for quotation, never sent.)"""
    _name = "stock.warehouse.orderpoint"
    _inherit = ["stock.warehouse.orderpoint", "bf.kitchen.scope.mixin"]

    bf_kitchen_type = fields.Selection(related="product_id.product_tmpl_id.bf_kitchen_type", string="Kitchen type")

    def _bf_kitchen_is_kitchen(self):
        # Manual rules only: an automatic rule set by the stock manager orders on its own at each exit;
        # its levels are the stock manager's.
        return self.filtered(lambda o: o.product_id.product_tmpl_id.bf_kitchen_type in STOCK_TYPES
                             and o.trigger == "manual")

    def _bf_kitchen_protected_fields(self):
        return RULE_PROTECTED

    def _bf_kitchen_check_location(self):
        for rule in self:
            root = rule.warehouse_id.view_location_id
            loc = rule.location_id
            if loc.usage != "internal" or not root or not loc.parent_path.startswith(root.parent_path):
                raise AccessError(_("As a kitchen manager, a stock level is set on a stock location of its "
                                    "warehouse (%s is not one).", loc.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        scoped = self._bf_kitchen_scope_applies("create")
        if scoped:
            for vals in vals_list:
                given = {f for f in ("route_id", "group_id") if vals.get(f)}
                if given:
                    self._bf_kitchen_refuse_fields(sorted(given))
                # A rule left to its native default (automatic) would order on its own at each exit.
                vals["trigger"] = "manual"
        rules = super().create(vals_list)._bf_kitchen_check_created(vals_list)
        if scoped:
            rules._bf_kitchen_check_location()
        return rules

    def write(self, vals):
        res = super().write(vals)
        if "location_id" in vals and self._bf_kitchen_scope_applies("write"):
            self._bf_kitchen_check_location()
        return res

    def _bf_kitchen_check_buyer(self):
        if self._bf_kitchen_scope_applies("write") and not self.env.user.has_group(BUYER):
            raise AccessError(_("Ordering takes the Purchase rights: the kitchen manager sets the stock "
                                "levels, purchasing orders (or use the order guide with the Purchase rights)."))

    def action_replenish(self, force_to_max=False):
        self._bf_kitchen_check_buyer()
        return super(StockWarehouseOrderpoint, self.with_context(bf_kitchen_keep_levels=KEEP_LEVELS)).action_replenish(
            force_to_max=force_to_max)

    def action_replenish_auto(self):
        self._bf_kitchen_check_buyer()
        return super().action_replenish_auto()

    @api.autovacuum
    def _unlink_processed_orderpoints(self):
        removed = super(StockWarehouseOrderpoint, self.with_context(
            bf_kitchen_keep_levels=KEEP_LEVELS))._unlink_processed_orderpoints()
        # Only what is really gone: the native replenishment report takes the returned rules out of the
        # existing ones and would make a second rule on the same item and location.
        return removed - removed.exists() if isinstance(removed, models.BaseModel) else removed

    def unlink(self):
        # Odoo removes the manual rules it made itself once they need nothing (the nightly clean-up,
        # after Order): a kitchen stock level is a setting, not a passing suggestion.
        if self.env.context.get("bf_kitchen_keep_levels") is KEEP_LEVELS:
            levels = self._bf_kitchen_is_kitchen().filtered(lambda r: r.product_min_qty or r.product_max_qty)
            return super(StockWarehouseOrderpoint, self - levels).unlink() if self - levels else True
        return super().unlink()


class KitchenOrderGuide(models.TransientModel):
    """What to order from one supplier for its next delivery: Odoo's forecast of each item (stock,
    receipts and deliveries under way), the known demand Odoo does not see yet (a bridge adds the
    school meals ordered), against the item's min and max; rounded up to the supplier's unit."""
    _name = "bf.kitchen.order.guide"
    _description = "Kitchen order guide"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, readonly=True)
    partner_id = fields.Many2one(
        "res.partner", "Supplier", required=True, check_company=True,
        domain="[('bf_food_supplier', '=', True)]")
    warehouse_id = fields.Many2one(
        "stock.warehouse", "Warehouse", required=True, check_company=True,
        default=lambda self: self.env.company._bf_kitchen_warehouse())
    delivery_date = fields.Date("For the delivery of", readonly=True)
    cover_until = fields.Date("Covering until", readonly=True,
                              help="The delivery after this one: the order covers what is needed until then.")
    computed = fields.Boolean(readonly=True)
    line_ids = fields.One2many("bf.kitchen.order.guide.line", "guide_id", "Items")
    order_id = fields.Many2one("purchase.order", "Request for quotation", readonly=True)

    @api.depends("partner_id", "delivery_date")
    def _compute_display_name(self):
        for guide in self:
            guide.display_name = _("Order guide: %s", guide.partner_id.name or "…")

    def _bf_check_manager(self):
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("The order guide is the kitchen manager's."))

    def _bf_known_demand(self, date_to):
        """{product: quantity in its stock unit} the kitchen knows it will use until `date_to` and
        Odoo's forecast does not count yet. A bridge adds to it (the school meals ordered)."""
        return {}

    def _bf_tz(self):
        """The kitchen's time zone: the company's, else its warehouse's address, else the user's."""
        return pytz.timezone(self.company_id.partner_id.tz or self.warehouse_id.partner_id.tz
                             or self.env.user.tz or "UTC")

    def _bf_today(self):
        return datetime.now(pytz.utc).astimezone(self._bf_tz()).date()

    def _bf_local_noon(self, day):
        """Noon of `day` in the kitchen's time zone, in UTC: a date at midnight UTC reads as the day before
        west of Greenwich (on the request for quotation sent to the supplier)."""
        return self._bf_tz().localize(datetime.combine(day, time(12, 0))).astimezone(pytz.utc).replace(tzinfo=None)

    def _bf_local_end(self, day):
        return self._bf_tz().localize(datetime.combine(day, time(23, 59, 59))).astimezone(pytz.utc).replace(tzinfo=None)

    def _bf_sellers(self, product, today):
        """The product's vendor lines this company can use today, preferred first (Odoo's order)."""
        company = self.company_id
        return product._prepare_sellers().filtered(
            lambda s: s.company_id in (company, self.env["res.company"])
            and (not s.product_id or s.product_id == product)
            and (not s.date_start or s.date_start <= today) and (not s.date_end or s.date_end >= today))

    def _bf_items(self, today):
        """The kitchen items this supplier is the one to order from: the supplier of the item's rule here,
        else its preferred vendor. An item sold by two suppliers is in one guide only."""
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id.commercial_partner_id
        Info = self.env["product.supplierinfo"].sudo()
        infos = Info.search([("partner_id", "child_of", partner.id), ("company_id", "in", (company.id, False))])
        Rule = self.env["stock.warehouse.orderpoint"].sudo()
        rules = Rule.search([("warehouse_id", "=", self.warehouse_id.id), ("company_id", "=", company.id),
                             ("product_id.product_tmpl_id.bf_kitchen_type", "in", STOCK_TYPES)])
        candidates = rules.product_id
        for info in infos:
            candidates |= info.product_id or info.product_tmpl_id.product_variant_ids
        root = self.warehouse_id.lot_stock_id
        items = []
        for product in candidates.sudo().with_company(company):
            if product.product_tmpl_id.bf_kitchen_type not in STOCK_TYPES or not product.is_storable \
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

    def action_compute(self):
        self.ensure_one()
        self._bf_check_manager()
        today = self._bf_today()
        delivery, after = self.partner_id._bf_kitchen_deliveries(today)
        items = self._bf_items(today)
        extra = self._bf_known_demand(after)
        wh = self.warehouse_id
        until = self._bf_local_end(after)
        products = self.env["product.product"].browse([p.id for p, _r in items])
        # Requests for quotation not confirmed yet: Odoo's forecast does not see them (purchase_stock).
        _by_location, in_progress = products.sudo()._get_quantity_in_progress(warehouse_ids=wh.ids)
        lines = []
        for product, rule in items:
            forecast = product.with_context(warehouse_id=wh.id, to_date=until).virtual_available
            pending = in_progress.get((product.id, wh.id), 0.0)
            known = extra.get(product, 0.0)
            free = forecast + pending - known
            rounding = product.uom_id.rounding
            if rule:
                low, high = rule.product_min_qty, rule.product_max_qty
                need = high - free if float_compare(free, low, precision_rounding=rounding) < 0 else 0.0
                if need > 0 and rule.qty_multiple > 0:
                    need = math.ceil(need / rule.qty_multiple - 1e-9) * rule.qty_multiple
            else:
                low = high = 0.0
                need = -free if float_compare(free, 0.0, precision_rounding=rounding) < 0 else 0.0
            sellers = self._bf_sellers(product, today).filtered(
                lambda s: s.partner_id.commercial_partner_id == self.partner_id.commercial_partner_id)
            uom = sellers[:1].product_uom or product.uom_po_id
            if uom.category_id != product.uom_id.category_id:
                uom = product.uom_id
            qty = 0.0
            if float_compare(need, 0.0, precision_rounding=rounding) > 0:
                qty = math.ceil(product.uom_id._compute_quantity(need, uom, round=False) - 1e-9)
                # The smallest tier's minimum: a bigger tier is cheaper, not required.
                qty = max(qty, min(sellers.mapped("min_qty") or [0.0]))
            info = product._select_seller(partner_id=self.partner_id, quantity=qty or None, date=today, uom_id=uom)
            lines.append(Command.create({
                "product_id": product.id, "uom_id": uom.id,
                "on_hand": product.with_context(warehouse_id=wh.id).qty_available,
                "forecast": forecast, "in_progress": pending, "known_demand": known, "min_qty": low, "max_qty": high,
                "suggested": qty, "qty": qty, "price_unit": info.price if info else 0.0,
                "supplierinfo_id": info.id if info else False}))
        self.line_ids.unlink()
        self.write({"line_ids": lines, "delivery_date": delivery, "cover_until": after, "computed": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "target": "current", "name": _("Order guide")}

    def action_create_rfq(self):
        """Odoo's own request for quotation, in the supplier's units, for the delivery day (at noon, in
        the company's time zone). Odoo picks the price: the tier, the currency and the discount for the
        real quantity. Made with the user's own purchase rights."""
        self.ensure_one()
        self._bf_check_manager()
        if not self.env.user.has_group(BUYER):
            raise AccessError(_("Creating a request for quotation takes the Purchase rights: ask purchasing, "
                                "or give the kitchen manager the Purchase user group."))
        lines = self.line_ids.filtered(lambda l: float_compare(l.qty, 0.0, precision_digits=4) > 0)
        if not lines:
            raise UserError(_("Nothing to order: every quantity is 0."))
        planned = self._bf_local_noon(self.delivery_date or self._bf_today())
        order = self.env["purchase.order"].with_company(self.company_id).create({
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "picking_type_id": self.warehouse_id.in_type_id.id,
            "date_planned": planned,
            "origin": _("Kitchen order guide"),
            "order_line": [Command.create({
                "product_id": line.product_id.id, "product_qty": line.qty, "product_uom": line.uom_id.id,
                "date_planned": planned}) for line in lines],
        })
        self.order_id = order
        return {"type": "ir.actions.act_window", "res_model": "purchase.order", "res_id": order.id,
                "view_mode": "form", "target": "current"}


class KitchenOrderGuideLine(models.TransientModel):
    _name = "bf.kitchen.order.guide.line"
    _description = "Kitchen order guide line"
    _order = "suggested desc, id"

    guide_id = fields.Many2one("bf.kitchen.order.guide", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="guide_id.company_id")
    product_id = fields.Many2one("product.product", "Item", required=True, readonly=True)
    stock_uom_id = fields.Many2one(related="product_id.uom_id", string="Stock unit")
    uom_id = fields.Many2one("uom.uom", "Ordered in", required=True, readonly=True)
    supplierinfo_id = fields.Many2one("product.supplierinfo", readonly=True)
    on_hand = fields.Float("On hand", digits="Product Unit of Measure", readonly=True)
    forecast = fields.Float("Forecast", digits="Product Unit of Measure", readonly=True,
                            help="Odoo's forecast at the next delivery after this one: stock, receipts and "
                                 "deliveries under way, in the stock unit.")
    in_progress = fields.Float("Being ordered", digits="Product Unit of Measure", readonly=True,
                               help="Requests for quotation not confirmed yet, in the stock unit.")
    known_demand = fields.Float("Known demand", digits="Product Unit of Measure", readonly=True,
                                help="What the kitchen knows it will use and Odoo does not count yet (school "
                                     "meals ordered), in the stock unit.")
    min_qty = fields.Float("Min", digits="Product Unit of Measure", readonly=True)
    max_qty = fields.Float("Max", digits="Product Unit of Measure", readonly=True)
    suggested = fields.Float("Suggested", digits="Product Unit of Measure", readonly=True)
    qty = fields.Float("To order", digits="Product Unit of Measure")
    price_unit = fields.Float("Price", digits="Product Price", readonly=True)
