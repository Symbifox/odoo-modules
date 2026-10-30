import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)


def bf_readable_cost(env, cost, uom, currency):
    """A cost per stock unit as a person reads it: per kg or per L when the unit is the g or
    the ml (0.0275 $/g reads 27.50 $ / kg). Returns (value, unit shown, text)."""
    if not uom:
        return cost, uom, formatLang(env, cost or 0.0, currency_obj=currency) if currency else ""
    reference = uom.category_id.uom_ids.filtered(lambda u: u.uom_type == "reference")[:1]
    shown = reference if reference and uom.factor > reference.factor else uom
    value = uom._compute_price(cost or 0.0, shown)
    money = formatLang(env, value, currency_obj=currency) if currency else formatLang(env, value)
    return value, shown, "%s / %s" % (money, shown.name)

KITCHEN_TYPES = [("ingredient", "Ingredient"), ("preparation", "Preparation"), ("dish", "Dish")]
COST_SOURCES = [("bill", "Vendor bill"), ("manual", "Entered"), ("recipe", "Recipe")]
MANAGER = "bf_kitchen.group_kitchen_manager"
# A kitchen cost is often per gram or per ml: the 2 decimals of "Product Price" would round
# 0.025 $/g to 0.03 and make the plate cost wrong by 20 %.
COST_DIGITS = (16, 6)
# What a kitchen manager writing products only through the kitchen access lines cannot change:
# taxes, accounts (every property_* field), cost, category (its valuation and accounts), company
# and type. They settle the accounting and the stock valuation, not the kitchen.
PROTECTED_FIELDS = {"taxes_id", "supplier_taxes_id", "standard_price", "categ_id", "company_id",
                    "type", "is_storable"}
# Written by the cost engine only, as superuser: the kitchen manager enters bf_cost_manual.
ENGINE_FIELDS = ("bf_cost_unit", "bf_cost_date", "bf_cost_source", "bf_cost_line_id", "bf_cost_manual_date")
# Context value that only server code can give (an RPC context is JSON): skips the refresh of a
# write made by the engine itself. A plain key would let any caller skip it.
REFRESHING = object()


class ProductTemplate(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "bf.kitchen.scope.mixin"]

    bf_kitchen_type = fields.Selection(KITCHEN_TYPES, "Kitchen type", index=True, tracking=True)
    bf_allergen_input_ids = fields.Many2many(
        "bf.food.allergen", "bf_kitchen_product_allergen_rel", "product_tmpl_id", "allergen_id",
        "Contains", help="Allergens declared on this product. A preparation or a dish also carries "
                         "the allergens of its recipe.")
    bf_may_contain_ids = fields.Many2many(
        "bf.food.allergen", "bf_kitchen_product_may_contain_rel", "product_tmpl_id", "allergen_id",
        "May contain", help="Cross-contamination: printed apart on the allergen sheet.")
    bf_allergen_ids = fields.Many2many(
        "bf.food.allergen", string="Allergens", compute="_compute_bf_allergen_ids",
        help="Declared allergens plus those of the recipe, sub-recipes included.")
    bf_yield_pct = fields.Float(
        "Yield (%)", default=100.0,
        help="Usable share after trimming or peeling: 45 for a whole salmon, 85 for carrots. "
             "Copied on each new recipe line, then changed there if needed.")
    bf_kitchen_bom_id = fields.Many2one("mrp.bom", "Recipe", compute="_compute_bf_kitchen_bom_id")
    # Template view of the variant costs (single variant), as Odoo does for standard_price.
    # Each with its own compute and inverse: sharing them, writing one recomputed the other from
    # the variant and lost what was entered.
    bf_cost_unit = fields.Float(
        "Kitchen cost", compute="_compute_bf_cost_unit", inverse="_inverse_bf_cost_unit",
        digits=COST_DIGITS, groups=MANAGER)
    bf_cost_date = fields.Date("Cost date", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_source = fields.Selection(COST_SOURCES, "Cost from", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_line_id = fields.Many2one(
        "account.move.line", "Cost bill line", compute="_compute_bf_cost", groups=MANAGER)
    bf_cost_manual = fields.Float(
        "Entered cost", compute="_compute_bf_cost_manual", inverse="_inverse_bf_cost_manual",
        digits=COST_DIGITS, groups=MANAGER,
        help="Used only until a validated vendor bill gives the cost.")

    # What a person reads: the kitchen cost per kg or per L when the stock unit is the g or the
    # ml (0.0275 $/g reads 27.50 $/kg), with the currency. The cost itself keeps its 6 decimals.
    bf_cost_display = fields.Monetary(
        "Kitchen cost", currency_field="currency_id", compute="_compute_bf_cost_display", groups=MANAGER)
    bf_cost_display_uom_id = fields.Many2one(
        "uom.uom", "Kitchen cost per", compute="_compute_bf_cost_display", groups=MANAGER)
    # The same in one piece for the form, where an amount and a unit side by side leave a gap.
    bf_cost_display_text = fields.Char(
        "Kitchen cost, as read", compute="_compute_bf_cost_display", groups=MANAGER)

    @api.depends_context("company", "lang")
    @api.depends("bf_cost_unit", "uom_id", "currency_id")
    def _compute_bf_cost_display(self):
        for tmpl in self:
            value, shown, text = bf_readable_cost(tmpl.env, tmpl.bf_cost_unit, tmpl.uom_id, tmpl.currency_id)
            tmpl.bf_cost_display = value
            tmpl.bf_cost_display_uom_id = shown
            tmpl.bf_cost_display_text = text

    @api.constrains("bf_yield_pct")
    def _check_bf_yield_pct(self):
        for tmpl in self:
            if tmpl.bf_kitchen_type and not 0 < tmpl.bf_yield_pct <= 100:
                raise ValidationError(_("The yield must be above 0 and at most 100%."))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._bf_kitchen_cost_vals(vals) for vals in vals_list]
        return super().create(vals_list)._bf_kitchen_check_created(vals_list)

    @api.model
    def _bf_kitchen_cost_vals(self, vals):
        """Kitchen cost and entered cost both given (a form creating a product sends every field):
        one value is kept, the entered cost when there is one, else the typed kitchen cost."""
        if "bf_cost_unit" in vals and "bf_cost_manual" in vals:
            vals = dict(vals)
            unit = vals.pop("bf_cost_unit")
            if not vals["bf_cost_manual"] and unit:
                vals["bf_cost_manual"] = unit
        return vals

    def _get_related_fields_variant_template(self):
        # Written again once the variant exists: at create, the inverse runs before it does.
        return super()._get_related_fields_variant_template() + ["bf_cost_manual", "bf_cost_unit"]

    def write(self, vals):
        res = super().write(self._bf_kitchen_cost_vals(vals))
        if "uom_id" in vals:
            # The cost is per stock unit: a new stock unit changes it, and the recipes using it.
            variants = self.filtered("bf_kitchen_type").product_variant_ids
            for company in self.env["res.company"].sudo().search([]):
                try:
                    with self.env.cr.savepoint():
                        variants.sudo()._bf_kitchen_refresh_costs(company)
                except Exception:
                    _logger.exception("Kitchen cost refresh failed for products %s", variants.ids)
        return res

    def _bf_kitchen_is_kitchen(self):
        return self.filtered("bf_kitchen_type")

    def _bf_kitchen_protected_fields(self):
        return PROTECTED_FIELDS

    def _compute_bf_kitchen_bom_id(self):
        boms = self.env["mrp.bom"]._bom_find(self.product_variant_ids)
        for tmpl in self:
            tmpl.bf_kitchen_bom_id = boms.get(tmpl.product_variant_id, False) if tmpl.product_variant_id else False

    def _bf_single_variant(self):
        self.ensure_one()
        return self.product_variant_id if self.product_variant_count == 1 else self.env["product.product"]

    @api.depends_context("company")
    @api.depends("product_variant_ids.bf_cost_date", "product_variant_ids.bf_cost_source",
                 "product_variant_ids.bf_cost_line_id")
    def _compute_bf_cost(self):
        for tmpl in self:
            variant = tmpl._bf_single_variant()
            tmpl.bf_cost_date = variant.bf_cost_date
            tmpl.bf_cost_source = variant.bf_cost_source
            tmpl.bf_cost_line_id = variant.bf_cost_line_id

    @api.depends_context("company")
    @api.depends("product_variant_ids.bf_cost_unit")
    def _compute_bf_cost_unit(self):
        for tmpl in self:
            tmpl.bf_cost_unit = tmpl._bf_single_variant().bf_cost_unit

    @api.depends_context("company")
    @api.depends("product_variant_ids.bf_cost_manual")
    def _compute_bf_cost_manual(self):
        for tmpl in self:
            tmpl.bf_cost_manual = tmpl._bf_single_variant().bf_cost_manual

    def _inverse_bf_cost_manual(self):
        for tmpl in self:
            variant = tmpl._bf_single_variant()
            if variant and abs(variant.bf_cost_manual - tmpl.bf_cost_manual) > 1e-9:
                variant.bf_cost_manual = tmpl.bf_cost_manual

    def _inverse_bf_cost_unit(self):
        """The kitchen cost typed on an ingredient with no bill yet is its entered cost.

        Compared with the value stored on the variant (``_origin`` is the record itself outside
        a form): unchanged, nothing is written.
        """
        for tmpl in self:
            variant = tmpl._bf_single_variant()
            if not variant or abs(variant.bf_cost_unit - tmpl.bf_cost_unit) <= 1e-9:
                continue
            if tmpl.bf_kitchen_type != "ingredient" or variant.bf_cost_source == "bill":
                raise UserError(_("%s: the kitchen cost comes from its vendor bills or its recipe and "
                                  "cannot be typed.", tmpl.display_name))
            variant.bf_cost_manual = tmpl.bf_cost_unit

    @api.depends("bf_allergen_input_ids", "bf_kitchen_type")
    def _compute_bf_allergen_ids(self):
        for tmpl in self:
            tmpl.bf_allergen_ids = tmpl._bf_kitchen_allergens(set())

    def _bf_kitchen_allergens(self, seen):
        self.ensure_one()
        found = self.bf_allergen_input_ids
        if self.id in seen:
            return found
        seen = seen | {self.id}
        if self.bf_kitchen_type in ("preparation", "dish") and self.product_variant_id:
            bom = self.env["mrp.bom"]._bom_find(self.product_variant_id).get(self.product_variant_id)
            for line in bom.bom_line_ids if bom else []:
                found |= line.product_id.product_tmpl_id._bf_kitchen_allergens(seen)
        return found


class ProductProduct(models.Model):
    _name = "product.product"
    _inherit = ["product.product", "bf.kitchen.scope.mixin"]

    # Per company and per variant: a second company in the same database has its own costs.
    bf_cost_unit = fields.Float(
        "Kitchen cost", company_dependent=True, digits=COST_DIGITS, groups=MANAGER,
        help="Cost of one stock unit: the last validated vendor bill, the entered cost until "
             "then, or the recipe for a preparation or a dish. The stock valuation is not touched.")
    bf_cost_date = fields.Date("Cost date", company_dependent=True, groups=MANAGER)
    bf_cost_source = fields.Selection(COST_SOURCES, "Cost from", company_dependent=True, groups=MANAGER)
    bf_cost_line_id = fields.Many2one(
        "account.move.line", "Cost bill line", company_dependent=True, groups=MANAGER)
    bf_cost_manual = fields.Float(
        "Entered cost", company_dependent=True, digits=COST_DIGITS, groups=MANAGER)
    bf_cost_manual_date = fields.Date("Entered on", company_dependent=True, groups=MANAGER)

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        if not self.env.su and any(p[f] for p in products for f in ENGINE_FIELDS):
            products._bf_kitchen_refuse_engine_fields()
        return products._bf_kitchen_check_created(vals_list)

    def _bf_kitchen_is_kitchen(self):
        return self.filtered(lambda p: p.product_tmpl_id.bf_kitchen_type)

    def _bf_kitchen_protected_fields(self):
        return PROTECTED_FIELDS

    def _bf_kitchen_refuse_engine_fields(self):
        raise AccessError(_("The kitchen cost is computed from the vendor bills and the recipes: "
                            "enter a cost in Entered cost instead."))

    def write(self, vals):
        if not self.env.su and set(ENGINE_FIELDS) & set(vals):
            self._bf_kitchen_refuse_engine_fields()
        if "bf_cost_manual" in vals:
            vals = dict(vals, bf_cost_manual_date=fields.Date.context_today(self))
        res = super().write(vals)
        if "bf_cost_manual" in vals and self.env.context.get("bf_kitchen_refreshing") is not REFRESHING:
            self.sudo()._bf_kitchen_refresh_costs(self.env.company)
        return res

    # ── cost engine ────────────────────────────────────────────────────────────────────

    def _bf_kitchen_line_unit_cost(self, line, company):
        """Cost of one stock unit of ``self`` from a posted vendor bill line, or (False, reason).

        Never raises: a validation must not be blocked by a kitchen cost. The unit categories
        are compared before converting, because ``_compute_quantity(raise_if_failure=False)``
        returns the quantity unconverted and would give a wrong price without any error.
        """
        self.ensure_one()
        if line.quantity <= 0 or line.balance <= 0:
            return False, False
        qty_stock, issue = line._bf_kitchen_stock_qty(self, company)
        if issue:
            return False, issue
        if qty_stock <= 0:
            return False, False
        amount = abs(line.balance)
        share = company.bf_kitchen_tax_nonrecoverable_pct
        if share and line.price_subtotal:
            taxes = (line.price_total - line.price_subtotal) * amount / abs(line.price_subtotal)
            amount += taxes * share / 100.0
        return amount / qty_stock, False

    def _bf_kitchen_cost_lines(self, company):
        """Posted vendor bill lines that can give ``self`` its cost, newest bill first."""
        self.ensure_one()
        return self.env["account.move.line"].sudo().search([
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "=", "in_invoice"),
            ("move_id.payment_state", "!=", "reversed"),
            ("display_type", "=", "product"),
            "|", ("product_id", "=", self.id), ("bf_ingredient_id", "=", self.id),
        ], order="invoice_date desc, id desc")

    def _bf_kitchen_ingredient_cost(self, company):
        """(unit cost, date, source, line) for an ingredient.

        Stops at the newest line that gives a cost: the lines of the bills being validated are
        flagged by the validation itself, and the nightly run (context ``bf_kitchen_full_scan``)
        reads every line again to flag the ones it cannot read.
        """
        self.ensure_one()
        full = self.env.context.get("bf_kitchen_full_scan")
        found = None
        for line in self._bf_kitchen_cost_lines(company):
            if line.move_id._bf_kitchen_is_refunded():
                continue
            cost, issue = self._bf_kitchen_line_unit_cost(line, company)
            if (line.bf_cost_issue or False) != (issue or False):
                line.sudo().bf_cost_issue = issue or False
            if cost and found is None:
                found = (cost, line.invoice_date or line.date, "bill", line)
                if not full:
                    break
        if found:
            return found
        product = self.with_company(company)
        if product.bf_cost_manual:
            return product.bf_cost_manual, product.bf_cost_manual_date, "manual", self.env["account.move.line"]
        return 0.0, False, False, self.env["account.move.line"]

    def _bf_kitchen_refresh_costs(self, company):
        """Recompute the kitchen cost of these products, then of every recipe that uses them.

        Level by level: the recipes reached are gathered first, then each one is computed once,
        after all of its components that changed (a dish made of an ingredient and of a sauce
        that uses the same ingredient waits for the sauce). A recipe none of whose components
        changed is not computed; a cost is written only when it changes. Returns
        {dish: (old cost, new cost)} for the dishes computed.

        Every product given is computed: the nightly run gives every kitchen product, so a stored
        cost gone wrong (an edit missed, an old version) is put right at every level. A
        preparation or a dish with no active recipe costs 0.
        """
        Product = self.env["product.product"].sudo().with_company(company).with_context(
            bf_kitchen_refreshing=REFRESHING)
        Line = self.env["account.move.line"]
        start = Product.browse(self.ids).filtered(lambda p: p.product_tmpl_id.bf_kitchen_type)
        # 1. The recipes reached: users[component] = the products whose recipe uses it.
        users, components = {}, {}
        reached, frontier = start, start
        while frontier:
            lines = self.env["mrp.bom.line"].sudo().search([
                ("product_id", "in", frontier.ids),
                ("bom_id.active", "=", True),
                ("bom_id.company_id", "in", [company.id, False]),
                ("bom_id.product_tmpl_id.bf_kitchen_type", "in", ["preparation", "dish"]),
            ])
            new = Product
            for line in lines:
                bom = line.bom_id
                parents = Product.browse((bom.product_id or bom.product_tmpl_id.product_variant_ids).ids)
                child = Product.browse(line.product_id.id)
                for parent in parents:
                    users.setdefault(child, set()).add(parent)
                    components.setdefault(parent, set()).add(child)
                    if parent not in reached:
                        new |= parent
            reached |= new
            frontier = new
        # 2. Level by level, from the products given up to the dishes.
        waiting = {p: len(components.get(p, set()) & set(reached)) for p in reached}
        dirty = set(start)
        done = set()
        dishes = {}
        level = Product.browse([p.id for p in reached if not waiting[p]])
        while True:
            if not level:
                rest = [p for p in reached if p not in done]
                if not rest:
                    break
                # A recipe that uses itself, directly or not: computed once, as is.
                _logger.warning("Kitchen recipes in a loop: %s", Product.browse([p.id for p in rest]).mapped("display_name"))
                level = Product.browse([p.id for p in rest])
            todo = level.filtered(lambda p: p in dirty)
            boms = self.env["mrp.bom"].sudo()._bom_find(
                todo.filtered(lambda p: p.product_tmpl_id.bf_kitchen_type != "ingredient"), company_id=company.id)
            for product in todo:
                old = product.bf_cost_unit
                if product.product_tmpl_id.bf_kitchen_type == "ingredient":
                    cost, date, source, line = product._bf_kitchen_ingredient_cost(company)
                else:
                    bom = boms.get(product)
                    if bom:
                        _total, cost, date = bom._bf_raw_cost(company)
                        source, line = "recipe", Line
                    else:
                        cost, date, source, line = 0.0, False, False, Line
                if product.product_tmpl_id.bf_kitchen_type == "dish":
                    dishes[product] = (old, cost)
                changed = (abs((old or 0.0) - (cost or 0.0)) > 1e-9 or product.bf_cost_date != (date or False)
                           or product.bf_cost_source != (source or False) or product.bf_cost_line_id != line)
                if changed:
                    product.write({"bf_cost_unit": cost, "bf_cost_date": date, "bf_cost_source": source,
                                   "bf_cost_line_id": line.id if line else False})
                if changed or product in start:
                    dirty.update(users.get(product, ()))
            done.update(level)
            nxt = []
            for product in level:
                for parent in users.get(product, ()):
                    if parent in done:
                        continue
                    waiting[parent] -= 1
                    if waiting[parent] == 0:
                        nxt.append(parent.id)
            level = Product.browse(list(dict.fromkeys(nxt)))
        return dishes

    @api.model
    def _cron_bf_kitchen_refresh_all(self):
        """Nightly safety net: every ingredient from its bills, then every preparation and dish
        from its recipe, each one computed again whatever its components did."""
        for company in self.env["res.company"].search([]):
            products = self.sudo().with_company(company).search([
                ("product_tmpl_id.bf_kitchen_type", "!=", False)])
            try:
                with self.env.cr.savepoint():
                    products.with_context(bf_kitchen_full_scan=True)._bf_kitchen_refresh_costs(company)
            except Exception:
                _logger.exception("Kitchen cost refresh failed for company %s", company.id)


class ProductSupplierinfo(models.Model):
    _name = "product.supplierinfo"
    _inherit = ["product.supplierinfo", "bf.kitchen.scope.mixin"]

    bf_pack_qty = fields.Float(
        "Stock units per billed unit", digits="Product Unit of Measure",
        help="This vendor's format, in the product's stock unit: 12000 for a case of 12 x 1 L "
             "when the stock unit is the ml. Used for the kitchen cost of its bill lines in the "
             "billed unit only.")
    bf_pack_uom_id = fields.Many2one(
        "uom.uom", "Billed unit",
        help="The unit of this vendor's bill lines that the format applies to. Empty: the lines "
             "with no unit. A line in any other unit is converted as usual.")

    @api.constrains("bf_pack_qty", "bf_pack_uom_id")
    def _check_bf_pack_qty(self):
        for info in self:
            if info.bf_pack_qty < 0 or (info.bf_pack_uom_id and info.bf_pack_qty <= 0):
                raise ValidationError(_("The format of a vendor line must be above 0 (or left empty, "
                                        "with no billed unit)."))

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)._bf_kitchen_check_created(vals_list)

    def _bf_kitchen_is_kitchen(self):
        return self.filtered(
            lambda s: (s.product_tmpl_id or s.product_id.product_tmpl_id).bf_kitchen_type
            and (not s.product_id or s.product_id.product_tmpl_id.bf_kitchen_type))
