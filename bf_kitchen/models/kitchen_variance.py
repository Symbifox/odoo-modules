from collections import defaultdict

from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero, format_date

from .kitchen_consumption import STOCK_TYPES

MANAGER = "bf_kitchen.group_kitchen_manager"
KINDS = [
    ("purchase", "Bought"),
    ("consumption", "Consumed by sales"),
    ("loss", "Losses"),
    ("used", "Used in preparations"),
    ("made", "Made in the kitchen"),
    ("unexplained", "Unexplained variance"),
    ("other", "Other moves"),
]


class KitchenVariance(models.TransientModel):
    """What the kitchen consumed between two counts, and what nobody can explain.

    Every sale has already taken its ingredients out of the stock (register, delivery, invoice,
    school meals, imported sales): what a count then adjusts is what the sales do not explain.
    The report reads the stock moves between the two counts and sorts them; the quantities are
    valued at today's kitchen cost of each item, whatever the stock valuation method.
    """
    _name = "bf.kitchen.variance"
    _description = "Kitchen variance between two counts"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company,
                                 readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", "Warehouse", required=True, check_company=True,
        default=lambda self: self.env.company._bf_kitchen_warehouse())
    count_from_id = fields.Many2one(
        "bf.kitchen.count", "From the count", check_company=True,
        domain="[('warehouse_id', '=', warehouse_id), ('state', '=', 'done')]")
    count_to_id = fields.Many2one(
        "bf.kitchen.count", "To the count", check_company=True,
        domain="[('warehouse_id', '=', warehouse_id), ('state', '=', 'done')]")
    date_from = fields.Datetime("From", readonly=True)
    date_to = fields.Datetime("To", readonly=True)
    computed = fields.Boolean(readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    line_ids = fields.One2many("bf.kitchen.variance.line", "variance_id", "Lines", readonly=True)
    top_line_ids = fields.Many2many("bf.kitchen.variance.line", compute="_compute_totals",
                                    string="Biggest unexplained variances")
    loss_line_ids = fields.Many2many("bf.kitchen.variance.line", compute="_compute_totals",
                                     string="Losses by reason")
    purchase_value = fields.Monetary("Bought", compute="_compute_totals", currency_field="currency_id")
    consumption_value = fields.Monetary("Consumed by sales", compute="_compute_totals",
                                        currency_field="currency_id")
    loss_value = fields.Monetary("Losses", compute="_compute_totals", currency_field="currency_id")
    unexplained_value = fields.Monetary("Unexplained variance", compute="_compute_totals",
                                        currency_field="currency_id")
    unexplained_pct = fields.Float("Unexplained (% of purchases)", compute="_compute_totals", digits=(16, 1))
    warning_html = fields.Html("To know", readonly=True)

    @api.depends("count_from_id", "count_to_id")
    def _compute_display_name(self):
        for report in self:
            report.display_name = _("Variance %(start)s → %(end)s", start=report.count_from_id.name or "…",
                                    end=report.count_to_id.name or "…")

    @api.onchange("warehouse_id")
    def _onchange_warehouse_id(self):
        counts = self.env["bf.kitchen.count"].search(
            [("warehouse_id", "=", self.warehouse_id.id), ("state", "=", "done")], order="date_done desc", limit=2)
        self.count_to_id = counts[:1]
        self.count_from_id = counts[1:2]

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if res.get("warehouse_id") and not res.get("count_to_id"):
            counts = self.env["bf.kitchen.count"].search(
                [("warehouse_id", "=", res["warehouse_id"]), ("state", "=", "done")],
                order="date_done desc", limit=2)
            res["count_to_id"] = counts[:1].id
            res["count_from_id"] = counts[1:2].id
        return res

    @api.depends("line_ids.value", "line_ids.kind")
    def _compute_totals(self):
        for report in self:
            lines = report.line_ids
            value = lambda kind: sum(lines.filtered(lambda l: l.kind == kind).mapped("value"))
            report.purchase_value = value("purchase")
            report.consumption_value = value("consumption")
            report.loss_value = value("loss")
            report.unexplained_value = value("unexplained")
            report.unexplained_pct = (100.0 * report.unexplained_value / report.purchase_value
                                      if report.purchase_value else 0.0)
            unexplained = lines.filtered(lambda l: l.kind == "unexplained" and not float_is_zero(
                l.value, precision_rounding=report.currency_id.rounding or 0.01))
            report.top_line_ids = unexplained.sorted(lambda l: -abs(l.value))[:10]
            report.loss_line_ids = lines.filtered(lambda l: l.kind == "loss").sorted(lambda l: -l.value)

    @staticmethod
    def _bf_count_end(count):
        """When a count closed the stock: its warehouse stock location, applied last. The stock
        location is where the sales take from, so a sale before that moment is in the count."""
        root = count.warehouse_id.lot_stock_id
        step = count.step_ids.filtered(lambda s: s.location_id == root and s.applied_date)
        return step[:1].applied_date or count.date_done

    def action_compute(self):
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("The variance report is the kitchen manager's."))
        if not self.count_from_id or not self.count_to_id:
            raise UserError(_("Choose the two finished counts to compare."))
        date_from = self._bf_count_end(self.count_from_id)
        date_to = self._bf_count_end(self.count_to_id)
        if not date_from or not date_to or date_from >= date_to:
            raise UserError(_("The first count must have finished before the second."))
        company = self.company_id
        Move = self.env["stock.move"].sudo()
        view = self.warehouse_id.view_location_id
        inside = set(self.env["stock.location"].sudo().with_context(active_test=False).search(
            [("id", "child_of", view.id), ("usage", "=", "internal")]).ids)
        moves = Move.search([
            ("state", "=", "done"), ("company_id", "=", company.id),
            ("date", ">", date_from), ("date", "<=", date_to),
            ("product_id.bf_kitchen_type", "in", STOCK_TYPES),
            "|", ("location_id", "in", list(inside)), ("location_dest_id", "in", list(inside)),
        ])
        Tag = self.env["stock.scrap.reason.tag"].sudo()
        rows = defaultdict(float)
        for move in moves:
            kind = move._bf_kitchen_variance_kind(inside)
            if not kind:
                continue
            kind, sign, reason_id, source = kind
            reason = Tag.browse(reason_id).name if reason_id else (_("No reason") if kind == "loss" else False)
            qty = move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id, round=False)
            rows[(move.product_id.id, kind, reason or False, source or False)] += sign * qty
        for key, qty in self._bf_variance_adjustments(date_from, date_to, moves):
            rows[key] += qty
        Product = self.env["product.product"].sudo().with_company(company)
        vals = []
        for (product_id, kind, reason, source), qty in rows.items():
            product = Product.browse(product_id)
            if float_is_zero(qty, precision_rounding=product.uom_id.rounding / 100.0):
                continue
            value = qty * product.bf_cost_unit
            vals.append({"product_id": product_id, "kind": kind, "reason": reason, "source": source,
                         "qty": qty, "value": value, "value_abs": abs(value)})
        self.line_ids.unlink()
        self.write({"line_ids": [Command.create(v) for v in vals], "date_from": date_from, "date_to": date_to,
                    "computed": True, "warning_html": self._bf_variance_warnings(date_from, date_to)})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current", "name": _("Kitchen variance")}

    def _bf_variance_adjustments(self, date_from, date_to, moves):
        """Rows a bridge moves from one column to another: [((product_id, kind, reason, source),
        quantity)]. A school meal cancelled after it was served is consumption turned into a loss."""
        return []

    def _bf_variance_warnings(self, date_from, date_to):
        """Consumptions recorded in the period for a day before it: the earlier count already saw
        them gone, so that count's variance was too negative and this one is too positive."""
        Picking = self.env["stock.picking"].sudo()
        late = Picking.search([
            ("company_id", "=", self.company_id.id), ("state", "=", "done"),
            ("picking_type_id.warehouse_id", "=", self.warehouse_id.id),
            ("date_done", ">", date_from), ("date_done", "<=", date_to),
            ("bf_kitchen_event_date", "!=", False),
            ("bf_kitchen_event_date", "<", fields.Date.context_today(self, date_from)),
        ])
        items = []
        if late:
            items.append(_("%(count)s consumption(s) recorded in this period are for days before the first "
                           "count (%(names)s): the first count already saw that food gone, and this "
                           "period's variance is that much too favourable.",
                           count=len(late), names=", ".join(late[:5].mapped("name"))))
            # The names are anyone's to write (a stock user renames a transfer): escaped below.
        between = self.env["bf.kitchen.count"].search_count([
            ("warehouse_id", "=", self.warehouse_id.id), ("state", "=", "done"),
            ("date_done", ">", self.count_from_id.date_done), ("date_done", "<", self.count_to_id.date_done)])
        if between:
            items.append(_("%s other count(s) were made between these two: their adjustments are in the "
                           "unexplained variance too.", between))
        if not items:
            return False
        return Markup("<ul>%s</ul>") % Markup("").join(Markup("<li>%s</li>") % escape(i) for i in items)

    def action_open_analysis(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Kitchen variance: analysis"),
            "res_model": "bf.kitchen.variance.line", "view_mode": "pivot,list",
            "views": [(self.env.ref("bf_kitchen.bf_kitchen_variance_line_view_pivot").id, "pivot"),
                      (self.env.ref("bf_kitchen.bf_kitchen_variance_line_view_list").id, "list")],
            "domain": [("variance_id", "=", self.id)],
        }


class KitchenVarianceLine(models.TransientModel):
    _name = "bf.kitchen.variance.line"
    _description = "Kitchen variance line"
    _order = "kind, value_abs desc, id"

    variance_id = fields.Many2one("bf.kitchen.variance", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="variance_id.company_id")
    currency_id = fields.Many2one(related="variance_id.currency_id")
    product_id = fields.Many2one("product.product", "Item", required=True, readonly=True)
    kitchen_type = fields.Selection(related="product_id.bf_kitchen_type", string="Kind of item")
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    kind = fields.Selection(KINDS, "Column", required=True, readonly=True)
    reason = fields.Char("Reason", readonly=True)
    source = fields.Char("Sold through", readonly=True)
    qty = fields.Float("Quantity", digits="Product Unit of Measure", readonly=True,
                       help="In the item's stock unit. Unexplained variance: counted minus expected.")
    value = fields.Monetary("Value", currency_field="currency_id", readonly=True,
                            help="At today's kitchen cost of the item.")
    value_abs = fields.Float(readonly=True)


class KitchenCount(models.Model):
    _inherit = "bf.kitchen.count"

    def action_bf_variance(self):
        """The variance between this count and the previous finished one of the warehouse."""
        self.ensure_one()
        if not self.env.user.has_group(MANAGER):
            raise AccessError(_("The variance report is the kitchen manager's."))
        previous = self.search([("warehouse_id", "=", self.warehouse_id.id), ("state", "=", "done"),
                                ("date_done", "<", self.date_done)], order="date_done desc", limit=1)
        report = self.env["bf.kitchen.variance"].with_company(self.company_id).create({
            "company_id": self.company_id.id, "warehouse_id": self.warehouse_id.id,
            "count_from_id": previous.id, "count_to_id": self.id})
        if previous:
            return report.action_compute()
        return {"type": "ir.actions.act_window", "res_model": report._name, "res_id": report.id,
                "view_mode": "form", "target": "current", "name": _("Kitchen variance")}
