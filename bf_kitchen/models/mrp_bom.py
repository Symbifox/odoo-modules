import logging
import math

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_is_zero
from odoo.tools.misc import formatLang

from .product import bf_readable_cost

_logger = logging.getLogger(__name__)

MANAGER = "bf_kitchen.group_kitchen_manager"
RECIPE_TYPES = ("preparation", "dish")
# What changes the cost of a recipe.
BOM_COST_FIELDS = {"product_qty", "product_uom_id", "bom_line_ids", "product_tmpl_id", "product_id",
                   "active", "company_id", "type"}
LINE_COST_FIELDS = {"product_id", "product_qty", "product_uom_id", "bom_id", "bf_net_qty"}
# Context value only server code can give (an RPC context is JSON): the recipe being saved
# refreshes its costs once, after its lines.
SAVING = object()


def _kitchen_products(boms):
    """The preparations and dishes these recipes make."""
    products = boms.env["product.product"]
    for bom in boms.filtered(lambda b: b.product_tmpl_id.bf_kitchen_type in RECIPE_TYPES):
        products |= bom.product_id or bom.product_tmpl_id.product_variant_ids
    return products


def _refresh_recipe_costs(env, products, companies):
    """Stored costs of these preparations and dishes, and of every recipe using them. Never
    raises: a recipe is saved even if its cost cannot be refreshed (the nightly run catches up)."""
    if not products:
        return
    for company in companies:
        try:
            with env.cr.savepoint():
                products.sudo()._bf_kitchen_refresh_costs(company)
        except Exception:
            _logger.exception("Kitchen cost refresh failed for recipes of %s", products.ids)


class MrpBom(models.Model):
    _name = "mrp.bom"
    _inherit = ["mrp.bom", "bf.kitchen.scope.mixin"]

    bf_kitchen_type = fields.Selection(related="product_tmpl_id.bf_kitchen_type", store=True, index=True)
    bf_method = fields.Html("Method", help="The steps, as the brigade reads them.")
    bf_prep_minutes = fields.Integer("Preparation time (min)")
    bf_target_pct = fields.Float(
        "Target food cost (%)", groups=MANAGER,
        default=lambda self: self.env.company.bf_kitchen_target_pct)
    bf_currency_id = fields.Many2one("res.currency", "Currency", compute="_compute_bf_currency_id")
    bf_cost_total = fields.Monetary(
        "Recipe cost", currency_field="bf_currency_id", compute="_compute_bf_cost", groups=MANAGER)
    # A preparation's cost per unit made, as a person reads it: per kg or per L when the
    # preparation is counted in g or ml (4.20 $ for 5 000 ml of stock reads 0.84 $ / L).
    bf_cost_unit_display = fields.Monetary(
        "Cost per unit made", currency_field="bf_currency_id", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_unit_display_uom_id = fields.Many2one(
        "uom.uom", "Per unit made", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_unit_display_text = fields.Char(
        "Cost per unit made, as read", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_portion = fields.Monetary(
        "Plate cost", currency_field="bf_currency_id", compute="_compute_bf_cost", groups=MANAGER,
        help="Cost of one unit of the product: one portion for a dish, one kg or one litre for a "
             "preparation measured that way.")
    bf_price = fields.Float(
        "Selling price", related="product_tmpl_id.list_price", readonly=False, groups=MANAGER,
        help="Before taxes.")
    bf_food_cost_pct = fields.Float("Food cost (%)", compute="_compute_bf_cost", groups=MANAGER)
    bf_price_suggested = fields.Monetary(
        "Suggested price", currency_field="bf_currency_id", compute="_compute_bf_cost", groups=MANAGER)
    bf_margin = fields.Monetary(
        "Margin per portion", currency_field="bf_currency_id", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_oldest_date = fields.Date("Oldest ingredient cost", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_stale = fields.Boolean("Cost too old", compute="_compute_bf_cost", groups=MANAGER)
    bf_over_target = fields.Boolean("Over target", compute="_compute_bf_cost", groups=MANAGER)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any("bf_kitchen_type" in vals for vals in vals_list):
            self._bf_kitchen_refuse_type()
        boms = super(MrpBom, self.with_context(bf_kitchen_recipe_saving=SAVING)).create(vals_list)
        boms = boms.with_env(self.env)._bf_kitchen_check_created(vals_list)
        if self.env.context.get("bf_kitchen_recipe_saving") is not SAVING:
            boms._bf_kitchen_recipes_changed(_kitchen_products(boms))
        return boms

    def write(self, vals):
        if not self.env.su and "bf_kitchen_type" in vals:
            self._bf_kitchen_refuse_type()
        touched = BOM_COST_FIELDS & set(vals) and self.env.context.get("bf_kitchen_recipe_saving") is not SAVING
        before = _kitchen_products(self) if touched else None
        res = super(MrpBom, self.with_context(bf_kitchen_recipe_saving=SAVING)).write(vals)
        if touched:
            self._bf_kitchen_recipes_changed(before | _kitchen_products(self))
        return res

    def unlink(self):
        products = _kitchen_products(self)
        companies = self._bf_kitchen_companies()
        res = super().unlink()
        _refresh_recipe_costs(self.env, products.exists(), companies)
        return res

    def _bf_kitchen_refuse_type(self):
        raise AccessError(_("The kitchen type of a recipe is the one of its product: change it on the product."))

    def _bf_kitchen_companies(self):
        """Kitchen costs are per company: a recipe shared by all companies refreshes them all."""
        if self and all(self.mapped("company_id")):
            return self.company_id
        return self.env["res.company"].sudo().search([])

    def _bf_kitchen_recipes_changed(self, products):
        _refresh_recipe_costs(self.env, products, self._bf_kitchen_companies())

    def _bf_kitchen_is_kitchen(self):
        # A recipe makes a preparation or a dish: a kit on an ingredient would explode its receipts.
        return self.filtered(lambda b: b.product_tmpl_id.bf_kitchen_type in RECIPE_TYPES)

    def _bf_company(self):
        return self.company_id or self.env.company

    # The type too: a preparation held in stock (normal) is no longer exploded at the sale, so a dish
    # takes it in its own stock unit, where a few grams of a stock kept in kg round to 0.
    @api.constrains("product_qty", "product_uom_id", "type")
    def _check_bf_portion_qty(self):
        """The quantity per portion of each line depends on the number of portions too: going
        from 1 to 1000 portions must not slip 3 g of salt under the stock unit's rounding."""
        self._bf_kitchen_check_portions()

    def _bf_kitchen_check_portions(self):
        """Refuse a recipe whose ingredient would never leave the stock.

        A kit is exploded into moves: Odoo rounds each leaf up to its line unit, the move then
        converts it to the stock unit rounding half-up; 0.3 g of salt stocked in kg gives
        0.00 kg. At the register this blocks the order's synchronisation; at delivery the
        ingredient is silently not taken out.

        - a preparation: each line as a whole (a sauce of 1000 g with 3 g of salt is fine; how
          much of it a dish takes is checked on the dish);
        - a dish: the ingredients of one portion, sub-recipes exploded as Odoo does;
        - a preparation that changes: the dishes that use it, directly or not, again.
        """
        boms = self.sudo().filtered(lambda b: b.product_tmpl_id.bf_kitchen_type in RECIPE_TYPES and b.active)
        preparations = boms.filtered(lambda b: b.product_tmpl_id.bf_kitchen_type == "preparation")
        for bom in preparations:
            for line in bom.bom_line_ids:
                bom._bf_kitchen_check_leaf(line, line.product_qty, False)
        dishes = (boms - preparations) | preparations._bf_kitchen_dishes_using()
        for bom in dishes:
            bom._bf_kitchen_check_dish()

    def _bf_kitchen_check_leaf(self, line, qty, dish):
        product = line.product_id
        if not product or not line.product_uom_id or line.product_uom_id.category_id != product.uom_id.category_id:
            return  # Odoo's own constraint refuses another category
        stock_qty = line.product_uom_id._compute_quantity(qty, product.uom_id, rounding_method="HALF-UP")
        if not float_is_zero(stock_qty, precision_rounding=product.uom_id.rounding):
            return
        values = {"product": product.display_name, "qty": round(qty, 4), "unit": line.product_uom_id.name,
                  "stock": product.uom_id.name, "recipe": self.product_tmpl_id.display_name}
        if dish:
            message = _("%(recipe)s: one portion takes %(qty)s %(unit)s of %(product)s, which becomes "
                        "0 %(stock)s in its stock unit, so it would never leave the stock. Stock "
                        "%(product)s in a smaller unit (g, ml).", **values)
        else:
            message = _("%(recipe)s: %(qty)s %(unit)s of %(product)s becomes 0 %(stock)s in its stock "
                        "unit, so it would never leave the stock. Stock %(product)s in a smaller unit "
                        "(g, ml).", **values)
        raise ValidationError(message)

    def _bf_kitchen_check_dish(self):
        self.ensure_one()
        product = self.product_id or self.product_tmpl_id.product_variant_id
        if not product or not self.product_qty:
            return
        # One portion is one unit of the dish, whatever the recipe is made in (a dozen, 10 portions).
        one = 1.0
        dish_uom = self.product_tmpl_id.uom_id
        if self.product_uom_id and dish_uom and self.product_uom_id.category_id == dish_uom.category_id:
            one = dish_uom._compute_quantity(1.0, self.product_uom_id, round=False)
        _boms, lines = self.explode(product, one / self.product_qty)
        for line, data in lines:
            self._bf_kitchen_check_leaf(line, data["qty"], True)

    def _bf_kitchen_dishes_using(self):
        """Active dish recipes using these preparations, through other preparations too."""
        Bom = self.env["mrp.bom"].sudo()
        dishes, seen = Bom, Bom
        frontier = self
        while frontier:
            seen |= frontier
            lines = self.env["mrp.bom.line"].sudo().search([
                ("product_id", "in", _kitchen_products(frontier).ids), ("bom_id.active", "=", True),
                ("bom_id.product_tmpl_id.bf_kitchen_type", "in", list(RECIPE_TYPES))])
            parents = lines.bom_id
            dishes |= parents.filtered(lambda b: b.product_tmpl_id.bf_kitchen_type == "dish")
            frontier = parents.filtered(lambda b: b.product_tmpl_id.bf_kitchen_type == "preparation") - seen
        return dishes

    @api.depends_context("company")
    def _compute_bf_currency_id(self):
        for bom in self:
            bom.bf_currency_id = bom._bf_company().currency_id

    @api.depends_context("company", "lang")
    @api.depends("product_qty", "product_uom_id", "bf_target_pct", "product_tmpl_id.list_price",
                 "product_tmpl_id.uom_id", "bom_line_ids.bf_cost_line", "bom_line_ids.product_id")
    def _compute_bf_cost(self):
        today = fields.Date.context_today(self)
        for bom in self:
            company = bom._bf_company()
            total, portion, oldest = bom._bf_raw_cost(company)
            price = bom.product_tmpl_id.list_price
            target = bom.bf_target_pct or company.bf_kitchen_target_pct
            bom.bf_cost_total = total
            bom.bf_cost_portion = portion
            (bom.bf_cost_unit_display, bom.bf_cost_unit_display_uom_id,
             bom.bf_cost_unit_display_text) = bf_readable_cost(
                bom.env, portion, bom.product_tmpl_id.uom_id, bom.bf_currency_id)
            bom.bf_food_cost_pct = portion / price * 100.0 if price else 0.0
            bom.bf_price_suggested = bom._bf_round_price(portion * 100.0 / target, company) if target else 0.0
            bom.bf_margin = price - portion
            bom.bf_cost_oldest_date = oldest
            bom.bf_cost_stale = bool(oldest) and (today - oldest).days > company.bf_kitchen_cost_max_age
            bom.bf_over_target = bool(price) and bom.bf_food_cost_pct > target

    def _bf_raw_cost(self, company):
        """(recipe cost, cost of one product unit, oldest ingredient cost date), unrounded.

        The cost engine carries these raw values up to the dishes: the Monetary fields round to
        the cent, and a sauce at 0.0123 $/g would otherwise become 0.01 $/g in every dish.
        """
        self.ensure_one()
        lines = self.sudo().with_company(company).bom_line_ids
        total = sum(line._bf_raw_cost(company) for line in lines)
        units = self.product_qty
        tmpl_uom = self.product_tmpl_id.uom_id
        if self.product_uom_id and tmpl_uom and self.product_uom_id.category_id == tmpl_uom.category_id:
            units = self.product_uom_id._compute_quantity(self.product_qty, tmpl_uom, round=False)
        dates = [d for d in lines.product_id.mapped("bf_cost_date") if d]
        return total, (total / units if units else 0.0), (min(dates) if dates else False)

    @api.model
    def _bf_round_price(self, price, company):
        if price <= 0:
            return 0.0
        rule = company.bf_kitchen_price_rounding
        if rule == "0.95":
            return math.ceil(price + 0.05 - 1e-9) - 0.05
        step = {"0.05": 0.05, "0.25": 0.25, "1": 1.0}.get(rule)
        if not step:
            return company.currency_id.round(price)
        return round(math.ceil(price / step - 1e-9) * step, 2)

    def _bf_format_number(self, value, digits=3):
        """A quantity or a percentage for the printed sheets, in the reader's language
        (decimal comma in French), without the trailing zeros: 120, 0,5, 33,4."""
        text = formatLang(self.env, value or 0.0, digits=digits)
        point = self.env["res.lang"]._get_data(code=self.env.lang or "en_US").decimal_point or "."
        if point in text:
            text = text.rstrip("0").rstrip(point)
        return text

    def action_bf_apply_suggested_price(self):
        for bom in self:
            if bom.bf_price_suggested:
                bom.product_tmpl_id.list_price = bom.bf_price_suggested
        return True


class MrpBomLine(models.Model):
    _name = "mrp.bom.line"
    _inherit = ["mrp.bom.line", "bf.kitchen.scope.mixin"]

    bf_yield_pct = fields.Float(
        "Yield (%)", help="Copied from the ingredient when the line is created, then kept as is.")
    bf_net_qty = fields.Float(
        "Net quantity", digits="Product Unit of Measure", compute="_compute_bf_net_qty",
        inverse="_inverse_bf_net_qty", help="What goes on the plate. The quantity that leaves the "
                                            "stock is the gross one: net divided by the yield.")
    bf_currency_id = fields.Many2one(related="bom_id.bf_currency_id", string="Currency")
    bf_cost_line = fields.Monetary(
        "Line cost", currency_field="bf_currency_id", compute="_compute_bf_cost_line", groups=MANAGER)

    @api.model_create_multi
    def create(self, vals_list):
        products = self.env["product.product"].browse([v["product_id"] for v in vals_list if v.get("product_id")])
        for vals in vals_list:
            if "bf_yield_pct" not in vals and vals.get("product_id"):
                vals["bf_yield_pct"] = products.browse(vals["product_id"]).bf_yield_pct or 100.0
        lines = super().create(vals_list)._bf_kitchen_check_created(vals_list)
        if self.env.context.get("bf_kitchen_recipe_saving") is not SAVING:
            lines.bom_id._bf_kitchen_recipes_changed(_kitchen_products(lines.bom_id))
        return lines

    def write(self, vals):
        touched = LINE_COST_FIELDS & set(vals) and self.env.context.get("bf_kitchen_recipe_saving") is not SAVING
        before = self.bom_id if touched else None
        res = super(MrpBomLine, self.with_context(bf_kitchen_recipe_saving=SAVING)).write(vals)
        if touched:
            boms = before | self.bom_id
            boms._bf_kitchen_recipes_changed(_kitchen_products(boms))
        return res

    def unlink(self):
        boms = self.bom_id
        res = super().unlink()
        if self.env.context.get("bf_kitchen_recipe_saving") is not SAVING:
            boms._bf_kitchen_recipes_changed(_kitchen_products(boms))
        return res

    def _bf_kitchen_is_kitchen(self):
        return self.filtered(lambda l: l.bom_id.product_tmpl_id.bf_kitchen_type in RECIPE_TYPES)

    @api.depends("product_qty", "bf_yield_pct")
    def _compute_bf_net_qty(self):
        for line in self:
            line.bf_net_qty = line.product_qty * (line.bf_yield_pct or 100.0) / 100.0

    def _inverse_bf_net_qty(self):
        for line in self:
            line.product_qty = line.bf_net_qty * 100.0 / (line.bf_yield_pct or 100.0)

    @api.onchange("bf_yield_pct")
    def _onchange_bf_yield_pct(self):
        # Changing the yield keeps the net quantity: the gross one follows.
        for line in self:
            if line.bf_yield_pct and line._origin.bf_yield_pct:
                net = line._origin.product_qty * line._origin.bf_yield_pct / 100.0
                line.product_qty = net * 100.0 / line.bf_yield_pct

    @api.onchange("product_id")
    def _onchange_bf_product_yield(self):
        for line in self:
            if line.product_id and not line._origin.id:
                line.bf_yield_pct = line.product_id.bf_yield_pct or 100.0

    @api.depends_context("company")
    @api.depends("product_id", "product_id.bf_cost_unit", "product_qty", "product_uom_id")
    def _compute_bf_cost_line(self):
        for line in self:
            company = line.bom_id._bf_company() if line.bom_id else self.env.company
            line.bf_cost_line = line._bf_raw_cost(company)

    def _bf_raw_cost(self, company):
        """Gross quantity times the kitchen cost of one stock unit, unrounded."""
        self.ensure_one()
        product = self.product_id.sudo().with_company(company)
        qty = self.product_qty
        if self.product_uom_id and product.uom_id and self.product_uom_id != product.uom_id:
            if self.product_uom_id.category_id != product.uom_id.category_id:
                return 0.0
            qty = self.product_uom_id._compute_quantity(qty, product.uom_id, round=False)
        return qty * product.bf_cost_unit

    @api.constrains("bf_yield_pct")
    def _check_bf_yield_pct(self):
        for line in self._bf_kitchen_is_kitchen():
            if not 0 < line.bf_yield_pct <= 100:
                raise ValidationError(_("The yield must be above 0 and at most 100%."))

    @api.constrains("product_qty", "product_uom_id", "product_id", "bom_id")
    def _check_bf_portion_qty(self):
        """See ``mrp.bom._bf_kitchen_check_portions``."""
        self.bom_id._bf_kitchen_check_portions()
