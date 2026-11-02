from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_is_zero
from odoo.tools.misc import formatLang

from .kitchen_consumption import SALE_TYPES

MANAGER = "bf_kitchen.group_kitchen_manager"
QUADRANT_SEQ = {"star": 1, "plowhorse": 2, "puzzle": 3, "dog": 4}
QUADRANTS = [
    ("star", "Star"),
    ("plowhorse", "Plowhorse"),
    ("puzzle", "Puzzle"),
    ("dog", "Dog"),
]
# Kasavana and Smith: a dish is popular when it sells at least 70 % of an even share of its section.
POPULARITY_FACTOR = 0.7


def _check_manager(env, company=None):
    if not env.user.has_group(MANAGER):
        raise AccessError(_("Menu engineering is the kitchen manager's: it shows the costs."))
    if company is not None and company not in env.companies:
        raise AccessError(_("This report is for a company you do not work in."))


def _company_tz(env, company):
    """The restaurant's time: the company's time zone, the user's only when the company has none."""
    return pytz.timezone(company.partner_id.tz or env.user.tz or "UTC")


class KitchenMenu(models.TransientModel):
    """Menu engineering: each dish of the menu by popularity and margin, section by section.

    Popularity is the dish's share of the portions its section sold; margin, what one portion leaves
    once its plate cost is paid (the price really obtained, less today's plate cost). The sales come
    from every source the kitchen knows (register, invoices, another register, school meals), through
    `_bf_menu_sales`, which the bridges extend."""
    _name = "bf.kitchen.menu"
    _description = "Menu engineering"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("From", required=True,
                            default=lambda self: fields.Date.context_today(self) - timedelta(days=28))
    date_to = fields.Date("To", required=True, default=lambda self: fields.Date.context_today(self) - timedelta(days=1))
    computed = fields.Boolean(readonly=True)
    line_ids = fields.One2many("bf.kitchen.menu.line", "menu_id", "Dishes", readonly=True)
    revenue = fields.Monetary("Sales", compute="_compute_totals", currency_field="currency_id")
    cost = fields.Monetary("Food cost of the sales", compute="_compute_totals", currency_field="currency_id")
    food_cost_pct = fields.Float("Food cost (%)", compute="_compute_totals", digits=(16, 1))
    margin = fields.Monetary("Margin", compute="_compute_totals", currency_field="currency_id")
    portions = fields.Float("Portions sold", compute="_compute_totals", digits=(16, 0))
    star_count = fields.Integer("Stars", compute="_compute_totals")
    plowhorse_count = fields.Integer("Plowhorses", compute="_compute_totals")
    puzzle_count = fields.Integer("Puzzles", compute="_compute_totals")
    dog_count = fields.Integer("Dogs", compute="_compute_totals")
    revenue_no_recipe = fields.Monetary(
        "Sales without a recipe", compute="_compute_totals", currency_field="currency_id",
        help="Dishes sold without a recipe: their cost is unknown, they are left out of the food cost and margin.")
    estimated = fields.Boolean("Some sales estimated", compute="_compute_totals",
                               help="Sales imported from another register carry quantities only: their "
                                    "sales are counted at the dish's selling price.")

    @api.depends("date_from", "date_to")
    def _compute_display_name(self):
        for menu in self:
            menu.display_name = _("Menu engineering %(start)s → %(end)s", start=menu.date_from or "…",
                                  end=menu.date_to or "…")

    @api.depends("line_ids.revenue", "line_ids.cost_total", "line_ids.quadrant", "line_ids.no_recipe")
    def _compute_totals(self):
        for menu in self:
            # A dish without a recipe has no cost: its sales would show as pure margin.
            menu.revenue_no_recipe = sum(menu.line_ids.filtered("no_recipe").mapped("revenue"))
            lines = menu.line_ids.filtered(lambda l: not l.no_recipe)
            menu.revenue = sum(lines.mapped("revenue"))
            menu.cost = sum(lines.mapped("cost_total"))
            menu.margin = menu.revenue - menu.cost
            menu.food_cost_pct = 100.0 * menu.cost / menu.revenue if menu.revenue else 0.0
            menu.portions = sum(lines.mapped("qty"))
            for key, _label in QUADRANTS:
                menu["%s_count" % key] = len(lines.filtered(lambda l: l.quadrant == key))
            menu.estimated = any(lines.mapped("estimated"))

    # --- the period, in the company's time
    def _bf_bounds(self):
        """The period's start and end (exclusive), as UTC datetimes: whole days in the restaurant's time,
        whoever looks (a manager abroad sees the restaurant's week)."""
        self.ensure_one()
        tz = _company_tz(self.env, self.company_id)
        start = tz.localize(datetime.combine(self.date_from, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(datetime.combine(self.date_to + timedelta(days=1), time.min)) \
            .astimezone(pytz.utc).replace(tzinfo=None)
        return start, end

    @api.model
    def _bf_local_days(self, company, start, end):
        """The days of a source dated by day (an invoice, an import, a school meal) that belong to
        [start, end): those whose end falls in (start, end]. Whole days for a period of days; between two
        counts, the day of a count goes to the period after it, never to both."""
        tz = _company_tz(self.env, company)
        first = pytz.utc.localize(start).astimezone(tz).date()
        last = pytz.utc.localize(end).astimezone(tz).date() - timedelta(days=1)
        return first, last

    # --- the sales of every source
    @api.model
    def _bf_menu_add(self, sales, product, qty, revenue, source, estimated=False):
        """Add a sale of a kitchen item to `sales` ({product id: row}): a dish, or an ingredient or a
        preparation sold as it is (a bottle of beer); anything else is left out. The matrix keeps the
        dishes; the food cost of a period takes them all."""
        kind = product.product_tmpl_id.bf_kitchen_type if product else False
        if kind not in SALE_TYPES:
            return
        row = sales.setdefault(product.id, {"qty": 0.0, "revenue": 0.0, "estimated": False, "sources": set(),
                                            "type": kind})
        row["qty"] += qty
        row["revenue"] += revenue
        row["estimated"] = row["estimated"] or estimated
        row["sources"].add(source)

    @api.model
    def _bf_menu_sales(self, company, start, end, warehouse=None):
        """{product id: {qty, revenue, estimated, sources, type}}: the kitchen items sold by `company` in
        [start, end) (UTC datetimes), revenue before taxes, returns deducted; with `warehouse`, only the
        sales that leave from it. Each bridge adds its source."""
        return {}

    # --- the matrix
    @api.model
    def _bf_menu_dishes(self, company):
        """The dish recipes of the menu: active dishes of this company (or of every company)."""
        boms = self.env["mrp.bom"].sudo().search([
            ("bf_kitchen_type", "=", "dish"), ("active", "=", True), ("type", "=", "phantom"),
            ("company_id", "in", [company.id, False])])
        # A dish that is not for sale (a staff meal, a component of a set menu) is not on the menu.
        return boms.filtered(lambda b: b.product_tmpl_id.sale_ok)

    def action_compute(self):
        self.ensure_one()
        _check_manager(self.env, self.company_id)
        if self.date_from > self.date_to:
            raise UserError(_("The period starts after it ends."))
        company = self.company_id
        start, end = self._bf_bounds()
        sales = {k: v for k, v in self._bf_menu_sales(company, start, end).items() if v["type"] == "dish"}
        Product = self.env["product.product"].sudo().with_company(company)
        boms = {}
        for bom in self._bf_menu_dishes(company):
            product = bom.product_id or bom.product_tmpl_id.product_variant_id
            boms.setdefault(product.id, bom)
        for product_id in sales:
            if product_id not in boms:
                product = Product.browse(product_id)
                bom = self.env["mrp.bom"].sudo()._bom_find(product, company_id=company.id, bom_type="phantom").get(product)
                if bom:
                    boms[product_id] = bom
        rows = []
        # A dish sold without a recipe: on the matrix, its cost unknown, not classified.
        for product_id in set(sales) - set(boms):
            sale = sales[product_id]
            product = Product.browse(product_id)
            price = sale["revenue"] / sale["qty"] if sale["qty"] else product.lst_price
            rows.append({
                "product_id": product_id, "bom_id": False, "section": self._bf_menu_section(product),
                "qty": sale["qty"], "revenue": sale["revenue"], "price_avg": price, "cost_portion": 0.0,
                "margin_unit": 0.0, "estimated": sale["estimated"], "no_recipe": True,
                "sources": ", ".join(sorted(sale["sources"])),
            })
        for product_id, bom in boms.items():
            product = Product.browse(product_id)
            sale = sales.get(product_id, {"qty": 0.0, "revenue": 0.0, "estimated": False, "sources": set()})
            portion = bom.with_company(company)._bf_raw_cost(company)[1]
            qty, revenue = sale["qty"], sale["revenue"]
            price = revenue / qty if qty else product.lst_price
            rows.append({
                "product_id": product_id, "bom_id": bom.id, "section": self._bf_menu_section(product),
                "qty": qty, "revenue": revenue, "price_avg": price, "cost_portion": portion,
                "margin_unit": price - portion, "estimated": sale["estimated"],
                "sources": ", ".join(sorted(sale["sources"])),
            })
        self._bf_classify(rows)
        self.line_ids.unlink()
        self.write({"line_ids": [Command.create(r) for r in rows], "computed": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current", "name": _("Menu engineering")}

    def action_open_matrix(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Menu engineering: the matrix"),
            "res_model": "bf.kitchen.menu.line", "view_mode": "kanban,list,pivot",
            "views": [(self.env.ref("bf_kitchen.bf_kitchen_menu_line_view_kanban").id, "kanban"),
                      (self.env.ref("bf_kitchen.bf_kitchen_menu_line_view_list").id, "list"),
                      (self.env.ref("bf_kitchen.bf_kitchen_menu_line_view_pivot").id, "pivot")],
            "domain": [("menu_id", "=", self.id)],
            "context": {"group_by": "quadrant"},
        }

    @api.model
    def _bf_menu_section(self, product):
        """The section of the menu a dish belongs to: its product category here; the register's category
        with bf_kitchen_pos (Starters, Mains, Desserts)."""
        return product.categ_id.display_name or _("Dishes")

    @api.model
    def _bf_classify(self, rows):
        """Kasavana-Smith, section by section: popular when the dish sells at least 70 % of an even share
        of the section's portions; profitable when its margin per portion reaches the section's average
        margin, weighted by what sold (the plain average when the section sold nothing)."""
        by_section = defaultdict(list)
        for row in rows:
            if not row.get("no_recipe"):
                by_section[row["section"]].append(row)
        for section in by_section.values():
            # Returns of an earlier period can leave a dish below zero: it weighs nothing, it does not
            # count against the others.
            total = sum(max(r["qty"], 0.0) for r in section)
            n = len(section)
            if total > 0:
                average = sum(r["margin_unit"] * max(r["qty"], 0.0) for r in section) / total
            else:
                average = sum(r["margin_unit"] for r in section) / n
            for row in section:
                share = max(row["qty"], 0.0) / total if total > 0 else 0.0
                row["popularity_pct"] = 100.0 * share
                row["popular"] = total > 0 and share >= POPULARITY_FACTOR / n
                row["profitable"] = row["margin_unit"] >= average - 1e-9
                row["section_margin_avg"] = average
                row["quadrant"] = {(True, True): "star", (True, False): "plowhorse",
                                   (False, True): "puzzle", (False, False): "dog"}[(row["popular"], row["profitable"])]
                row["quadrant_seq"] = QUADRANT_SEQ[row["quadrant"]]
        return rows


class KitchenMenuLine(models.TransientModel):
    _name = "bf.kitchen.menu.line"
    _description = "Menu engineering: dish"
    _order = "section, quadrant_seq, margin_total desc"

    menu_id = fields.Many2one("bf.kitchen.menu", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="menu_id.currency_id")
    product_id = fields.Many2one("product.product", "Dish", readonly=True)
    bom_id = fields.Many2one("mrp.bom", "Recipe", readonly=True)
    section = fields.Char(readonly=True, help="The section of the menu (the register's category, or the product "
                                              "category): dishes are compared within their section.")
    qty = fields.Float("Portions sold", digits=(16, 0), readonly=True)
    revenue = fields.Monetary("Sales", currency_field="currency_id", readonly=True)
    price_avg = fields.Monetary("Average price", currency_field="currency_id", readonly=True,
                                help="The price really obtained (sales / portions); the selling price when "
                                     "nothing sold.")
    cost_portion = fields.Monetary("Plate cost", currency_field="currency_id", readonly=True,
                                   help="Today's plate cost of the recipe.")
    cost_total = fields.Monetary("Food cost", currency_field="currency_id", compute="_compute_totals", store=True)
    margin_unit = fields.Monetary("Margin per portion", currency_field="currency_id", readonly=True)
    margin_total = fields.Monetary("Total margin", currency_field="currency_id", compute="_compute_totals",
                                   store=True)
    food_cost_pct = fields.Float("Food cost (%)", compute="_compute_totals", store=True, digits=(16, 1))
    popularity_pct = fields.Float("Share of the section (%)", readonly=True, digits=(16, 1))
    section_margin_avg = fields.Monetary("Section's average margin", currency_field="currency_id", readonly=True)
    popular = fields.Boolean(readonly=True)
    profitable = fields.Boolean(readonly=True)
    quadrant = fields.Selection(QUADRANTS, readonly=True, index=True, group_expand="_read_group_quadrant")
    quadrant_seq = fields.Integer(readonly=True, default=9, help="The stars first, the dogs last, then the dishes "
                                                               "without a recipe.")
    no_recipe = fields.Boolean("No recipe", readonly=True, help="Sold, but without a recipe: its cost is unknown.")
    advice = fields.Char(compute="_compute_advice")
    summary = fields.Char(compute="_compute_advice")
    estimated = fields.Boolean("Sales estimated", readonly=True,
                               help="Imported from another register without amounts: counted at the selling price.")
    sources = fields.Char(readonly=True)

    @api.model
    def _read_group_quadrant(self, values, domain):
        """The matrix reads from the stars to the dogs, empty quadrants included."""
        return [key for key, _label in QUADRANTS]

    @api.depends("qty", "cost_portion", "margin_unit", "revenue", "no_recipe")
    def _compute_totals(self):
        for line in self:
            line.cost_total = line.qty * line.cost_portion
            line.margin_total = 0.0 if line.no_recipe else line.qty * line.margin_unit
            price = line.price_avg
            line.food_cost_pct = 100.0 * line.cost_portion / price if price and not line.no_recipe else 0.0

    @api.depends("quadrant", "qty", "popularity_pct", "no_recipe")
    def _compute_advice(self):
        advice = {
            "star": _("Keep it as it is: in view, consistent, its price watched."),
            "plowhorse": _("Popular, thin margin: lower its cost (portion, recipe) or raise its price a little."),
            "puzzle": _("Good margin, few sold: show it better (place on the menu, name, the servers' "
                        "suggestion), or lower its price."),
            "dog": _("Few sold, thin margin: replace it, or keep it for a good reason."),
        }
        for line in self:
            line.advice = (_("Sold without a recipe: its cost is unknown. Give it its recipe.") if line.no_recipe
                           else advice.get(line.quadrant, ""))
            line.summary = (_("%s portions", int(round(line.qty))) if line.no_recipe else
                            _("%(portions)s portions, %(share)s %% of the section", portions=int(round(line.qty)),
                              share=formatLang(self.env, line.popularity_pct, digits=1)))


class KitchenPriceImpact(models.TransientModel):
    """If an ingredient goes up: the dishes that use it, sub-recipes included, their cost and food cost
    before and after, and the ones that go over their target."""
    _name = "bf.kitchen.price.impact"
    _description = "Effect of an ingredient price increase"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    product_ids = fields.Many2many(
        "product.product", string="Ingredients", required=True,
        domain="[('bf_kitchen_type', 'in', ('ingredient', 'preparation'))]")
    increase_pct = fields.Float("Increase (%)", default=10.0, required=True)
    computed = fields.Boolean(readonly=True)
    line_ids = fields.One2many("bf.kitchen.price.impact.line", "impact_id", "Dishes", readonly=True)
    over_count = fields.Integer("Dishes going over their target", compute="_compute_over_count")

    @api.depends("product_ids", "increase_pct")
    def _compute_display_name(self):
        for impact in self:
            impact.display_name = _("If %(items)s go up %(pct)s %%", items=", ".join(impact.product_ids.mapped("name")) or "…",
                                    pct=formatLang(self.env, impact.increase_pct, digits=1 if impact.increase_pct % 1 else 0))

    @api.depends("line_ids.newly_over")
    def _compute_over_count(self):
        for impact in self:
            impact.over_count = len(impact.line_ids.filtered("newly_over"))

    @api.model
    def _bf_exposure(self, bom, company, stop, depth=0):
        """{product id: quantity in its stock unit} of the leaves of one unit of `bom`'s product: the
        ingredients, and the preparations through their own recipes, except those in `stop` (chosen
        themselves). The same arithmetic as the cost engine."""
        if depth > 20:
            return {}
        exposure = defaultdict(float)
        Bom = self.env["mrp.bom"].sudo()
        for line in bom.sudo().with_company(company).bom_line_ids:
            product = line.product_id.with_company(company)
            qty = line.product_qty
            if line.product_uom_id and product.uom_id and line.product_uom_id != product.uom_id:
                if line.product_uom_id.category_id != product.uom_id.category_id:
                    continue
                qty = line.product_uom_id._compute_quantity(qty, product.uom_id, round=False)
            sub = False
            # Through every component made from a recipe: a preparation, or a dish in a set menu (as the
            # cost engine), except the items chosen themselves.
            if product.id not in stop and product.product_tmpl_id.bf_kitchen_type in ("preparation", "dish"):
                sub = Bom._bom_find(product, company_id=company.id).get(product)
            if sub and sub.product_tmpl_id.bf_kitchen_type in ("preparation", "dish"):
                for leaf, leaf_qty in self._bf_exposure(sub, company, stop, depth + 1).items():
                    exposure[leaf] += qty * leaf_qty
            else:
                exposure[product.id] += qty
        units = bom.product_qty
        tmpl_uom = bom.product_tmpl_id.uom_id
        if bom.product_uom_id and tmpl_uom and bom.product_uom_id.category_id == tmpl_uom.category_id:
            units = bom.product_uom_id._compute_quantity(bom.product_qty, tmpl_uom, round=False)
        return {k: v / units for k, v in exposure.items()} if units else {}

    def action_compute(self):
        self.ensure_one()
        _check_manager(self.env, self.company_id)
        if not self.product_ids:
            raise UserError(_("Choose the ingredients that go up."))
        company = self.company_id
        stop = set(self.product_ids.ids)
        Product = self.env["product.product"].sudo().with_company(company)
        costs = {p.id: Product.browse(p.id).bf_cost_unit for p in self.product_ids}
        factor = self.increase_pct / 100.0
        rows = []
        for bom in self.env["bf.kitchen.menu"]._bf_menu_dishes(company):
            exposure = self._bf_exposure(bom, company, stop)
            delta = sum(exposure.get(pid, 0.0) * cost * factor for pid, cost in costs.items())
            if float_is_zero(delta, precision_digits=6):
                continue
            bom = bom.with_company(company)
            before = bom._bf_raw_cost(company)[1]
            after = before + delta
            price = bom.product_tmpl_id.list_price
            target = bom.bf_target_pct or company.bf_kitchen_target_pct
            pct_before = 100.0 * before / price if price else 0.0
            pct_after = 100.0 * after / price if price else 0.0
            rows.append({
                "bom_id": bom.id, "product_tmpl_id": bom.product_tmpl_id.id, "price": price,
                "cost_before": before, "cost_after": after, "pct_before": pct_before, "pct_after": pct_after,
                "target_pct": target, "over_after": bool(price) and pct_after > target,
                "newly_over": bool(price) and pct_after > target >= pct_before,
                # A price to reach the target again, for a dish that goes over it; none for the others.
                "price_suggested": (bom._bf_round_price(after * 100.0 / target, company)
                                    if target and price and pct_after > target else 0.0),
            })
        self.line_ids.unlink()
        self.write({"line_ids": [Command.create(r) for r in rows], "computed": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current", "name": _("If an ingredient goes up")}


class KitchenPriceImpactLine(models.TransientModel):
    _name = "bf.kitchen.price.impact.line"
    _description = "Effect of an ingredient price increase: dish"
    _order = "newly_over desc, pct_after desc"

    impact_id = fields.Many2one("bf.kitchen.price.impact", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="impact_id.currency_id")
    bom_id = fields.Many2one("mrp.bom", "Recipe", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", "Dish", readonly=True)
    price = fields.Monetary("Selling price", currency_field="currency_id", readonly=True)
    cost_before = fields.Monetary("Plate cost now", currency_field="currency_id", readonly=True)
    cost_after = fields.Monetary("Plate cost after", currency_field="currency_id", readonly=True)
    pct_before = fields.Float("Food cost now (%)", digits=(16, 1), readonly=True)
    pct_after = fields.Float("Food cost after (%)", digits=(16, 1), readonly=True)
    target_pct = fields.Float("Target (%)", digits=(16, 1), readonly=True)
    over_after = fields.Boolean("Over its target after", readonly=True)
    newly_over = fields.Boolean("Goes over its target", readonly=True)
    price_suggested = fields.Monetary("Suggested price after", currency_field="currency_id", readonly=True,
                                      help="The price that brings a dish over its target back to it.")
