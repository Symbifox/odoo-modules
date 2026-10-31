from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero

COOK = "bf_kitchen.group_kitchen_cook"
MANAGER = "bf_kitchen.group_kitchen_manager"
COUNTED_TYPES = ("ingredient", "preparation")
FREQUENCY_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}
# What only the module's own actions write (readonly in a view protects nothing: RPC writes any
# field). A forged state would let a cook apply a location twice (putting back what was sold
# since) or delete a count that moved the stock.
COUNT_SERVER_FIELDS = {"state", "date", "date_done", "company_id", "step_ids", "name"}
STEP_SERVER_FIELDS = {"state", "applied_date", "count_id", "location_id", "sequence", "company_id"}
# `is_counted` is the cook's: marking an item counted with nothing entered is counting 0.
LINE_SERVER_FIELDS = {"qty_before", "diff_qty", "diff_value", "step_id", "count_id", "company_id"}


def _without_defaults(env, reserved=None):
    """The context without the `default_*` keys of the server's own fields (all of them when
    `reserved` is None): a default passed by RPC would set what `_refuse_server_fields` refuses."""
    ctx = {k: v for k, v in env.context.items()
           if not (k.startswith("default_") and (reserved is None or k[8:] in reserved))}
    return ctx


def _refuse_server_fields(records, vals, reserved, what):
    if records.env.su:
        return
    forced = reserved & set(vals)
    if forced:
        raise AccessError(_("%(what)s: %(fields)s is written by the count itself, not by hand.",
                            what=what, fields=", ".join(sorted(forced))))


def _quant_domain(product, location):
    """The stock a kitchen count reads and sets: no lot, no package, no owner. A count line and
    its application must read the same stock, or the item is counted twice."""
    return [("product_id", "=", product.id), ("location_id", "=", location.id),
            ("lot_id", "=", False), ("package_id", "=", False), ("owner_id", "=", False)]


class KitchenCount(models.Model):
    """A count of the kitchen stock, one step per storage location.

    Each step is applied as soon as it is entered: a count applied hours later would put back
    in stock what was sold in between (Odoo sets the quant to the counted quantity, not to the
    counted quantity minus the sales since). The warehouse stock location itself is a step too:
    sales, receipts and preparations move the stock there, and it can be negative.
    """
    _name = "bf.kitchen.count"
    _description = "Kitchen count"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char("Reference", readonly=True, copy=False, default=lambda self: _("New"))
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company,
                                 readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", "Warehouse", required=True, check_company=True,
        default=lambda self: self.env.company._bf_kitchen_warehouse())
    user_id = fields.Many2one("res.users", "Counted by", default=lambda self: self.env.user)
    date = fields.Datetime("Started on", readonly=True, copy=False)
    date_done = fields.Datetime("Finished on", readonly=True, copy=False)
    state = fields.Selection([("draft", "To start"), ("in_progress", "In progress"), ("done", "Done")],
                             default="draft", required=True, readonly=True, copy=False, tracking=True)
    step_ids = fields.One2many("bf.kitchen.count.step", "count_id", "Locations", readonly=True)
    step_count = fields.Integer(compute="_compute_progress")
    step_done_count = fields.Integer(compute="_compute_progress")
    warning_html = fields.Html("Before counting", compute="_compute_warning_html", sanitize=False)
    currency_id = fields.Many2one(related="company_id.currency_id")
    diff_value = fields.Monetary(
        "Variance at kitchen cost", compute="_compute_diff_value", currency_field="currency_id",
        groups=MANAGER,
        help="Counted minus expected, valued at the kitchen cost of each item on the day of the count.")

    @api.depends("step_ids.state")
    def _compute_progress(self):
        for count in self:
            count.step_count = len(count.step_ids)
            count.step_done_count = len(count.step_ids.filtered(lambda s: s.state != "to_count"))

    @api.depends("step_ids.line_ids.diff_value")
    def _compute_diff_value(self):
        for count in self:
            count.diff_value = sum(count.sudo().step_ids.line_ids.mapped("diff_value"))

    @api.depends("warehouse_id", "state")
    def _compute_warning_html(self):
        for count in self:
            count.warning_html = count._bf_warnings() if count.state != "done" else False

    def _bf_warnings(self):
        """What makes a count wrong if it is applied now: stock moves that have not happened yet
        in Odoo (a transfer the register could not validate, an order still open at a table)."""
        self.ensure_one()
        if not self.warehouse_id:
            return False
        items = []
        Picking = self.env["stock.picking"].sudo()
        pending = Picking.search_count([
            ("picking_type_id.warehouse_id", "=", self.warehouse_id.id),
            ("state", "not in", ("draft", "done", "cancel")),
            ("scheduled_date", "<=", fields.Datetime.now()),
        ])
        if pending:
            items.append(_("%s transfer(s) of this warehouse are not validated: what they move is "
                           "not in the stock figures yet.", pending))
        if "pos.order" in self.env:
            Order = self.env["pos.order"].sudo()
            open_orders = Order.search_count([("state", "=", "draft"), ("company_id", "=", self.company_id.id)])
            if open_orders:
                items.append(_("%s register order(s) are still open: their dishes have not left "
                               "the stock yet.", open_orders))
            if self.company_id.sudo().point_of_sale_update_stock_quantities == "closing":
                paid = Order.search_count([("state", "=", "paid"), ("company_id", "=", self.company_id.id),
                                           ("session_id.state", "!=", "closed")])
                if paid:
                    items.append(_("%s register order(s) of a session still open leave the stock only "
                                   "when the session closes: close it before counting.", paid))
        if not items:
            return False
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % i for i in items)

    # ── steps ─────────────────────────────────────────────────────────────────────────

    def _bf_count_locations(self):
        """Every internal location of the warehouse stock, kitchen locations first, the stock
        location itself last (what is not put away, and the negative balances)."""
        self.ensure_one()
        root = self.warehouse_id.lot_stock_id
        locations = self.env["stock.location"].search([
            ("id", "child_of", root.id), ("usage", "=", "internal"),
            ("company_id", "in", (self.company_id.id, False))])
        children = (locations - root).sorted(lambda l: (l.bf_kitchen_count_sequence, l.complete_name))
        return children + root

    def action_start(self):
        for count in self:
            count._bf_check_cook()
            if count.state != "draft":
                continue
            count.check_access("write")
            if self.sudo().search_count([("warehouse_id", "=", count.warehouse_id.id),
                                         ("state", "=", "in_progress"), ("id", "!=", count.id)]):
                raise UserError(_("Another count of this warehouse is in progress: finish it first. Two "
                                  "counts at once would set the same stock twice."))
            count.sudo().write({"state": "in_progress", "date": fields.Datetime.now(),
                                "user_id": count.user_id.id or self.env.user.id})
            Step = self.env["bf.kitchen.count.step"].sudo().with_context(_without_defaults(self.env))
            for sequence, location in enumerate(count._bf_count_locations(), start=1):
                step = Step.create({"count_id": count.id, "location_id": location.id, "sequence": sequence})
                step._bf_fill_lines()
            count.activity_ids.filtered(
                lambda a: a.activity_type_id == self.env.ref("bf_kitchen.mail_activity_kitchen_count")
            ).action_done()
        return True

    def action_finish(self):
        for count in self:
            count._bf_check_cook()
            if count.state != "in_progress":
                continue
            count.check_access("write")
            root = count.warehouse_id.lot_stock_id
            root_step = count.step_ids.filtered(lambda s: s.location_id == root)
            if root_step and root_step.state != "applied":
                raise UserError(_(
                    "Apply the step %(location)s before finishing: sales, receipts and preparations "
                    "move the stock there, and a count that skips it counts that stock twice.",
                    location=root.complete_name))
            count.step_ids.filtered(lambda s: s.state == "to_count").sudo().write({"state": "skipped"})
            count.sudo().write({"state": "done", "date_done": fields.Datetime.now()})
        return True

    def _bf_check_cook(self):
        if not self.env.su and not self.env.user.has_group(COOK):
            raise AccessError(_("Counting the kitchen takes the Cook or Kitchen manager rights."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            self = self.with_context(_without_defaults(self.env, COUNT_SERVER_FIELDS - {"company_id"}))
        for vals in vals_list:
            _refuse_server_fields(self, {k for k in vals if k != "company_id"}, COUNT_SERVER_FIELDS, _("Kitchen count"))
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("bf.kitchen.count") or _("New")
        return super().create(vals_list)

    def write(self, vals):
        _refuse_server_fields(self, vals, COUNT_SERVER_FIELDS, _("Kitchen count"))
        if not self.env.su and "warehouse_id" in vals and any(c.state != "draft" for c in self):
            raise UserError(_("The warehouse of a count that has started cannot change."))
        return super().write(vals)

    def unlink(self):
        if any(count.state != "draft" for count in self):
            raise UserError(_("A count that has started cannot be deleted: its applied steps moved the stock."))
        return super().unlink()

    # ── reminder ──────────────────────────────────────────────────────────────────────

    @api.model
    def _cron_bf_kitchen_count_reminder(self):
        """For each company that asks for it, a count to start, with an activity for the kitchen
        manager, once the last finished count is older than the chosen frequency."""
        activity_type = self.env.ref("bf_kitchen.mail_activity_kitchen_count", raise_if_not_found=False)
        for company in self.env["res.company"].search([("bf_kitchen_count_frequency", "!=", False)]):
            days = FREQUENCY_DAYS.get(company.bf_kitchen_count_frequency)
            if not days:
                continue
            Count = self.sudo().with_company(company)
            if Count.search_count([("company_id", "=", company.id), ("state", "!=", "done")]):
                continue
            last = Count.search([("company_id", "=", company.id), ("state", "=", "done")],
                                order="date_done desc", limit=1)
            if last and last.date_done > fields.Datetime.now() - timedelta(days=days):
                continue
            warehouse = company._bf_kitchen_warehouse()
            if not warehouse:
                continue
            # Without a kitchen manager, the count is still prepared: it waits in the counts not finished.
            manager = company.bf_kitchen_manager_id
            count = Count.create({"company_id": company.id, "warehouse_id": warehouse.id,
                                  "user_id": manager.id or False})
            if activity_type and manager:
                count.activity_schedule(activity_type_id=activity_type.id, user_id=manager.id,
                                        date_deadline=fields.Date.context_today(self))


class KitchenCountStep(models.Model):
    _name = "bf.kitchen.count.step"
    _description = "Kitchen count: one location"
    _order = "count_id, sequence, id"
    _check_company_auto = True

    count_id = fields.Many2one("bf.kitchen.count", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="count_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    location_id = fields.Many2one("stock.location", "Location", required=True, check_company=True)
    state = fields.Selection([("to_count", "To count"), ("applied", "Applied"), ("skipped", "Skipped")],
                             default="to_count", required=True, readonly=True)
    applied_date = fields.Datetime("Applied on", readonly=True)
    line_ids = fields.One2many("bf.kitchen.count.line", "step_id", "Items")
    line_count = fields.Integer(compute="_compute_line_count")
    is_stock_root = fields.Boolean(compute="_compute_is_stock_root")

    @api.depends("line_ids")
    def _compute_line_count(self):
        for step in self:
            step.line_count = len(step.line_ids)

    @api.depends("location_id", "count_id.warehouse_id")
    def _compute_is_stock_root(self):
        for step in self:
            step.is_stock_root = step.location_id == step.count_id.warehouse_id.lot_stock_id

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("The locations of a count are made when it starts."))
        return super().create(vals_list)

    def write(self, vals):
        _refuse_server_fields(self, vals, STEP_SERVER_FIELDS, _("Kitchen count location"))
        return super().write(vals)

    def _bf_fill_lines(self):
        """One line per kitchen item that has stock here (negative balances included) or is
        stored here. Items tracked by lot, and stock in a package or owned by someone else, are
        left to Odoo's own inventory."""
        self.ensure_one()
        Quant = self.env["stock.quant"].sudo()
        quants = Quant.search([("location_id", "=", self.location_id.id),
                               ("lot_id", "=", False), ("package_id", "=", False), ("owner_id", "=", False),
                               ("product_id.tracking", "=", "none"),
                               ("product_id.product_tmpl_id.bf_kitchen_type", "in", COUNTED_TYPES)])
        products = quants.filtered(lambda q: not float_is_zero(
            q.quantity, precision_rounding=q.product_uom_id.rounding)).product_id
        stored = self.env["product.product"].sudo().with_company(self.company_id).search([
            ("product_tmpl_id.bf_kitchen_type", "in", COUNTED_TYPES), ("is_storable", "=", True),
            ("tracking", "=", "none"), ("product_tmpl_id.bf_kitchen_location_id", "=", self.location_id.id)])
        products |= stored
        # A preparation made on the fly is a kit: Odoo counts its components, never the kit itself.
        products = products.with_company(self.company_id).filtered(lambda p: not p.is_kits)
        products -= self.line_ids.product_id
        Line = self.env["bf.kitchen.count.line"].sudo().with_context(_without_defaults(self.env))
        for product in products.sorted("display_name"):
            Line.create({"step_id": self.id, "product_id": product.id})

    def action_open(self):
        self.ensure_one()
        if self.state == "to_count" and self.count_id.state == "in_progress":
            # What reached this location since the count started (a receipt, a sale below zero).
            self.check_access("read")
            self.sudo()._bf_fill_lines()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current",
                "views": [(self.env.ref("bf_kitchen.bf_kitchen_count_step_view_form").id, "form")]}

    def action_apply(self):
        """Set the stock of this location to what was counted, line by line, now."""
        for step in self:
            step.count_id._bf_check_cook()
            step.check_access("write")
            if step.count_id.state != "in_progress" or step.state != "to_count":
                raise UserError(_("Only a location still to count, in a count in progress, can be applied."))
            step._bf_check_location()
            # The stock location is applied last: sales take from it, so its moment closes the count
            # (the variance report reads the period up to it). A location applied after it would put
            # its adjustment in the next period.
            if not step.is_stock_root and step.count_id.step_ids.filtered(
                    lambda s: s.is_stock_root and s.state == "applied"):
                raise UserError(_("The stock location is already applied: it closes the count. "
                                  "Count %s before it, in the next count.", step.location_id.complete_name))
            lines = step.line_ids.filtered("is_counted")
            if not lines and step.line_ids:
                raise UserError(_("Nothing is counted at %s. Enter 0 for an item that is not there.",
                                  step.location_id.complete_name))
            # A location with nothing listed (an empty stock location) is applied as it is.
            Quant = self.env["stock.quant"].with_context(inventory_mode=True)
            quants = Quant
            for line in lines:
                line._bf_check_product()
                # Odoo can hold two quants of the same item here until its daily merge: the count
                # sets the first one, the others go to 0, and the expected stock is their sum.
                found = Quant.search(_quant_domain(line.product_id, step.location_id), order="id")
                before = sum(found.mapped("quantity"))
                if found:
                    found[0].inventory_quantity = line.counted_qty
                    for extra in found[1:]:
                        extra.inventory_quantity = 0.0
                else:
                    found = Quant.create({"product_id": line.product_id.id,
                                          "location_id": step.location_id.id,
                                          "inventory_quantity": line.counted_qty})
                line.sudo().write({"qty_before": before})
                quants |= found
            quants.filtered(lambda q: q.inventory_quantity_set)._apply_inventory()
            for line in lines:
                line._bf_store_diff()
            step.sudo().write({"state": "applied", "applied_date": fields.Datetime.now()})
        return True

    def _bf_check_location(self):
        """A stock location of the count's warehouse: no step may set the stock of a customer or
        partner location, or of another company."""
        for step in self:
            root = step.count_id.warehouse_id.lot_stock_id
            location = step.location_id
            if location.usage != "internal" or location.company_id not in (step.company_id, self.env["res.company"]) \
                    or not location.parent_path.startswith(root.parent_path):
                raise UserError(_("%s is not a stock location of this warehouse.", location.complete_name))

    def action_skip(self):
        for step in self:
            step.count_id._bf_check_cook()
            step.check_access("write")
            if step.is_stock_root:
                raise UserError(_(
                    "The stock location cannot be skipped: sales, receipts and preparations move the "
                    "stock there. Enter 0 for the items that are put away elsewhere."))
            if step.state == "to_count":
                step.sudo().state = "skipped"
        return True

    def action_back(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "bf.kitchen.count", "res_id": self.count_id.id,
                "view_mode": "form", "target": "current"}


class KitchenCountLine(models.Model):
    _name = "bf.kitchen.count.line"
    _description = "Kitchen count: one item"
    _order = "step_id, product_id"
    _check_company_auto = True

    step_id = fields.Many2one("bf.kitchen.count.step", required=True, ondelete="cascade", index=True)
    count_id = fields.Many2one(related="step_id.count_id", store=True)
    company_id = fields.Many2one(related="step_id.company_id", store=True)
    location_id = fields.Many2one(related="step_id.location_id")
    step_state = fields.Selection(related="step_id.state")
    product_id = fields.Many2one(
        "product.product", "Item", required=True, check_company=True,
        domain="[('product_tmpl_id.bf_kitchen_type', 'in', ('ingredient', 'preparation')), ('is_storable', '=', True)]")
    uom_id = fields.Many2one(related="product_id.uom_id", string="Stock unit")
    purchase_uom_id = fields.Many2one("uom.uom", "Purchase unit", compute="_compute_purchase_uom_id")
    qty_purchase = fields.Float("In purchase units", digits="Product Unit of Measure",
                                help="Whole purchase units (cases, bags, bottles).")
    qty_stock = fields.Float("Plus, in stock units", digits="Product Unit of Measure",
                             help="What is left of an opened unit, in the stock unit (g, ml, unit).")
    is_counted = fields.Boolean("Counted", help="Counted, even at 0. Entering a quantity marks it.")
    counted_qty = fields.Float("Counted", compute="_compute_counted_qty", digits="Product Unit of Measure")
    expected_qty = fields.Float("Expected", compute="_compute_expected_qty", digits="Product Unit of Measure")
    qty_before = fields.Float("Expected when applied", readonly=True, digits="Product Unit of Measure")
    diff_qty = fields.Float("Variance", readonly=True, digits="Product Unit of Measure")
    currency_id = fields.Many2one(related="company_id.currency_id")
    diff_value = fields.Monetary("Variance value", readonly=True, currency_field="currency_id", groups=MANAGER)

    _sql_constraints = [("product_step_uniq", "unique(step_id, product_id)",
                         "An item appears once per location.")]

    @api.depends("product_id")
    def _compute_purchase_uom_id(self):
        for line in self:
            po = line.product_id.uom_po_id
            line.purchase_uom_id = po if po and po.category_id == line.product_id.uom_id.category_id \
                and po != line.product_id.uom_id else False

    @api.depends("qty_purchase", "qty_stock", "purchase_uom_id")
    def _compute_counted_qty(self):
        for line in self:
            whole = line.purchase_uom_id._compute_quantity(line.qty_purchase, line.product_id.uom_id, round=False) \
                if line.purchase_uom_id else line.qty_purchase
            line.counted_qty = whole + line.qty_stock

    @api.depends("product_id", "location_id", "step_state")
    def _compute_expected_qty(self):
        for line in self:
            if line.step_state != "to_count":
                line.expected_qty = line.qty_before
                continue
            line.expected_qty = sum(self.env["stock.quant"].sudo().search(
                _quant_domain(line.product_id, line.location_id)).mapped("quantity")) if line.product_id else 0.0

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            self = self.with_context(_without_defaults(self.env, LINE_SERVER_FIELDS - {"step_id"}))
        for vals in vals_list:
            _refuse_server_fields(self, {k for k in vals if k != "step_id"}, LINE_SERVER_FIELDS, _("Kitchen count item"))
            if "qty_purchase" in vals or "qty_stock" in vals:
                vals["is_counted"] = True
        lines = super().create(vals_list)
        lines._bf_check_editable()
        lines._bf_check_product()
        return lines

    def write(self, vals):
        _refuse_server_fields(self, vals, LINE_SERVER_FIELDS, _("Kitchen count item"))
        self._bf_check_editable()
        if "product_id" in vals and not self.env.su and self.filtered("is_counted"):
            raise UserError(_("Change the item before entering its count, or add a new line."))
        if "qty_purchase" in vals or "qty_stock" in vals:
            vals = dict(vals, is_counted=True)
        result = super().write(vals)
        if "product_id" in vals:
            self._bf_check_product()
        return result

    def unlink(self):
        self._bf_check_editable()
        return super().unlink()

    def _bf_check_product(self):
        """A kitchen item kept in stock, not tracked by lot: what a kitchen count can set."""
        for line in self:
            product = line.product_id.with_company(line.company_id)
            if product.product_tmpl_id.bf_kitchen_type not in COUNTED_TYPES or not product.is_storable \
                    or product.tracking != "none" or product.is_kits:
                raise UserError(_(
                    "%s cannot be counted here: a kitchen count takes the ingredients and the preparations "
                    "held in stock, not tracked by lot. Count the ingredients of a preparation made on the "
                    "fly; use the Inventory app for the items tracked by lot.",
                    product.display_name))

    def _bf_check_editable(self):
        if self.env.su:
            return
        if not self.env.user.has_group(COOK):
            raise AccessError(_("Counting the kitchen takes the Cook or Kitchen manager rights."))
        if any(line.step_id.state != "to_count" or line.count_id.state != "in_progress" for line in self):
            raise UserError(_("A location that is applied or skipped cannot be changed."))

    def _bf_store_diff(self):
        """Counted minus expected, and its value at the kitchen cost of the day (as superuser:
        the cook applies, the value is for the manager)."""
        for line in self.sudo():
            diff = line.counted_qty - line.qty_before
            cost = line.product_id.with_company(line.company_id).bf_cost_unit
            super(KitchenCountLine, line).write({"diff_qty": diff, "diff_value": diff * cost})

    def action_zero(self):
        self.check_access("write")
        self.write({"qty_purchase": 0.0, "qty_stock": 0.0})
        return True
