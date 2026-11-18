# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models
from odoo.tools import float_compare

from .res_company import arrondir

_logger = logging.getLogger(__name__)

MANAGER = "bf_retail.group_retail_manager"


class ProductProduct(models.Model):
    _inherit = "product.product"

    # Followed or not is not a secret: a shelf clerk sees which products the store follows.
    bf_retail_managed = fields.Boolean(
        "Followed at retail", compute="_compute_bf_retail_managed", store=True,
        help="Set by the product's category, or by a category above it.")

    # The link the computes below hang their dependencies on: without it nothing would tell
    # the cache that a cost row just landed, and a price proposed in the same request as the
    # bill that moved the cost would be computed against the cost from before it.
    bf_retail_cost_ids = fields.One2many(
        "bf.retail.cost", "product_id", string="Cost series", groups=MANAGER)

    # 🔴 None of the computed fields below is stored. A cost, a margin and a suggested price
    # are read in ONE company (the series is per company), and a stored compute would be
    # computed once, in whichever company happened to write first, then served to every other.
    bf_retail_cost_id = fields.Many2one(
        "bf.retail.cost", "Current cost row", compute="_compute_bf_retail_cost", groups=MANAGER)
    bf_retail_cost = fields.Float(
        "Retail cost", digits=(16, 4), compute="_compute_bf_retail_cost", groups=MANAGER,
        help="The last cost observed for this product in this company.")
    bf_retail_cost_date = fields.Date(
        "Cost of", compute="_compute_bf_retail_cost", groups=MANAGER)
    bf_retail_cost_source = fields.Selection(
        selection=lambda self: self.env["bf.retail.cost"]._fields["source"].selection,
        string="Cost from", compute="_compute_bf_retail_cost", groups=MANAGER)
    bf_retail_cost_stale = fields.Boolean(
        "Cost is old", compute="_compute_bf_retail_cost", groups=MANAGER)
    bf_retail_cost_count = fields.Integer(
        "Costs observed", compute="_compute_bf_retail_cost", groups=MANAGER)

    bf_retail_price_untaxed = fields.Monetary(
        "Price excluding tax", currency_field="currency_id", compute="_compute_bf_retail_price",
        groups=MANAGER,
        help="The price on the shelf, with the taxes of the sale taken out. The margin is "
             "computed on this one, so a price that includes GST and a price that excludes "
             "the sales taxes give the same margin.")
    bf_retail_target_pct = fields.Float(
        "Target margin (%)", digits=(16, 1), compute="_compute_bf_retail_price", groups=MANAGER)
    bf_retail_margin_pct = fields.Float(
        "Margin (%)", digits=(16, 1), compute="_compute_bf_retail_price", groups=MANAGER)
    bf_retail_price_suggested = fields.Monetary(
        "Suggested price", currency_field="currency_id", compute="_compute_bf_retail_price",
        groups=MANAGER, help="The price that reaches the target margin, taken up to the "
                             "rounding of the category.")
    bf_retail_price_gap = fields.Monetary(
        "Gap", currency_field="currency_id", compute="_compute_bf_retail_price", groups=MANAGER,
        help="Suggested price less the price on the shelf.")

    @api.depends("categ_id", "categ_id.bf_retail_managed", "categ_id.parent_path")
    def _compute_bf_retail_managed(self):
        for product in self:
            product.bf_retail_managed = product.categ_id._bf_retail_managed_here() \
                if product.categ_id else False

    # `tz` with `company`: the age of a cost is read against the reader's today, and without
    # it the cache serves the day of whoever read first -- the exact shape the company key
    # already cost us once.
    @api.depends("bf_retail_cost_ids.cost", "bf_retail_cost_ids.date",
                 "bf_retail_cost_ids.company_id")
    @api.depends_context("company", "tz")
    def _compute_bf_retail_cost(self):
        Cost = self.env["bf.retail.cost"]
        company = self.env.company
        today = fields.Date.context_today(self)
        max_age = company.bf_retail_cost_max_age or 0
        counts = {}
        if self.ids:
            grouped = Cost.sudo()._read_group(
                [("product_id", "in", self.ids), ("company_id", "=", company.id)],
                groupby=["product_id"], aggregates=["__count"])
            counts = {product.id: count for product, count in grouped}
        for product in self:
            row = Cost._bf_last(product, company)
            product.bf_retail_cost_id = row
            product.bf_retail_cost = row.cost
            product.bf_retail_cost_date = row.date
            product.bf_retail_cost_source = row.source
            product.bf_retail_cost_count = counts.get(product.id, 0)
            # `_bf_last` never hands back a row dated in the FUTURE, so the age below is
            # never a negative number of days: a cost that is not in force yet is not the
            # current cost, and it is not old either (C11, and D9 of lot 2).
            product.bf_retail_cost_stale = bool(row.date) and max_age > 0 \
                and (today - row.date).days > max_age

    def _bf_retail_sale_taxes(self, company):
        """The taxes of the sale that belong to THIS store, and no fallback.

        🔴 The fallback `or self.taxes_id` applied another company's tax when none of the
        product's taxes were this one's: the margin came out at 50.0 % where it was 56.5 %.
        Read under sudo on purpose: the record rule of `account.tax` stops at the reader's own
        companies, and reading the tax of a product shared with another store raised an
        AccessError that took the whole Retail page down with it.
        """
        return self.sudo().taxes_id.filtered(
            lambda t: not t.company_id or t.company_id == company)

    def _bf_retail_untaxed(self, shown, company):
        """What a unit sold at `shown` leaves, taxes of the sale taken out.

        `shown` is the price written on the product, which is what the shelf shows: tax
        included where the taxes are included in the price (New Zealand), tax excluded
        elsewhere (Quebec). Odoo's own computation is asked about THAT price rather than
        about a probe: it rounds to the cent, and a ratio read off a probe then carried to
        another price is wrong by that rounding.
        """
        self.ensure_one()
        taxes = self._bf_retail_sale_taxes(company)
        if not taxes or not hasattr(taxes, "compute_all"):
            return shown
        return taxes.compute_all(shown, currency=company.currency_id, quantity=1.0,
                                 product=self)["total_excluded"]

    def _bf_retail_shown_for(self, untaxed, company):
        """The price to show for an untaxed amount: the inverse of the line above.

        Odoo's computation is affine in the price for percentage and fixed taxes, included or
        not, so two probes give the line. Far apart, and the slope is what matters: the
        intercept carries the rounding of the probes, and the caller checks the result
        against the real computation anyway.
        """
        self.ensure_one()
        taxes = self._bf_retail_sale_taxes(company)
        if not taxes or not hasattr(taxes, "compute_all"):
            return untaxed
        currency = company.currency_id
        probe = lambda price: taxes.compute_all(
            price, currency=currency, quantity=1.0, product=self)["total_excluded"]
        e1, e2 = probe(1000.0), probe(2000.0)
        a = (e2 - e1) / 1000.0
        if abs(a) < 1e-9:
            return untaxed
        b = e1 - a * 1000.0
        # An intercept of a cent or less is the rounding of the probes, not a fixed tax: the
        # slope read off two rounded probes carries about that much error at this scale.
        if abs(b) <= (currency.rounding or 0.01):
            b = 0.0
        return (untaxed - b) / a

    def _bf_retail_suggested(self, cost, company):
        """The price to show so that the margin excluding tax reaches the category's target.

        Rounded UP to the category's ending, then checked against Odoo's own computation: the
        inverse above is affine and the ending moves the price again, so the only honest way
        to promise "never below the target" is to read the margin of the price that came out
        and raise it by a cent until it holds.
        """
        self.ensure_one()
        target = self.categ_id._bf_retail_target(company) if self.categ_id else 0.0
        if not cost or target <= 0.0 or target >= 1.0:
            return 0.0, target
        untaxed_target = cost / (1.0 - target)
        rule = self.categ_id._bf_retail_rounding(company) if self.categ_id else "none"
        shown = arrondir(self._bf_retail_shown_for(untaxed_target, company), rule)
        # At most a few cents of correction: beyond that the taxes are not affine and the
        # price is left as it is rather than walked up for ever.
        for _ in range(5):
            if self._bf_retail_untaxed(shown, company) >= untaxed_target - 1e-9:
                break
            shown = arrondir(shown + 0.01, rule)
        else:
            # Said out loud rather than abandoned: at this point the price shown is BELOW the
            # target margin, which is the one thing this method promises not to do.
            _logger.warning(
                "bf_retail: %s left at %s, still under the target margin of %.1f %% in %s "
                "after five cents of correction (a tax that is not affine in the price?)",
                self.display_name, shown, target * 100.0, company.display_name)
        return shown, target

    # lst_price, not list_price: on a product with variants it carries the variant's own
    # extra, and that is the number the shelf shows. The dependencies are what lst_price is
    # made of, since a non-stored compute cannot be depended on reliably.
    # `uom` with `company`: lst_price is read in the unit of the context and carries that key
    # itself; without it here, the price computed in dozens was served to the unit. And the
    # company of a cost row is declared, as the cost compute declares it: a cost that changes
    # company left the suggested price on the value from before.
    @api.depends("list_price", "price_extra", "taxes_id", "categ_id",
                 "categ_id.bf_retail_margin_target", "categ_id.bf_retail_price_rounding",
                 "bf_retail_cost_ids.cost", "bf_retail_cost_ids.date",
                 "bf_retail_cost_ids.company_id")
    @api.depends_context("company", "uom")
    def _compute_bf_retail_price(self):
        company = self.env.company
        for product in self:
            cost = product.bf_retail_cost
            shown = product.lst_price
            untaxed = product._bf_retail_untaxed(shown, company)
            suggested, target = product._bf_retail_suggested(cost, company)
            product.bf_retail_price_untaxed = untaxed
            product.bf_retail_target_pct = target * 100.0
            product.bf_retail_margin_pct = ((untaxed - cost) / untaxed * 100.0) if untaxed else 0.0
            product.bf_retail_price_suggested = suggested
            product.bf_retail_price_gap = (suggested - shown) if suggested else 0.0

    def _bf_retail_price_extra_value(self):
        """The attribute value that can carry THIS variant's price, sisters left alone.

        A template with one variant has its own price and nothing to share. With several, the
        price of a variant is its template's price plus its own extra, so the extra is where a
        variant's price is written -- but only on an attribute value
        no other variant of the model carries. On a grid (two attributes crossed) no value
        belongs to one variant alone, and the batch names the product instead of moving a
        sister's price.
        """
        self.ensure_one()
        template = self.product_tmpl_id
        if len(template.product_variant_ids) <= 1:
            return self.env["product.template.attribute.value"]
        sisters = (template.product_variant_ids - self).product_template_attribute_value_ids
        return (self.product_template_attribute_value_ids - sisters)[:1]

    def _bf_retail_price_on_its_own(self):
        """True when this product's shelf price can be written without moving another one."""
        self.ensure_one()
        return len(self.product_tmpl_id.product_variant_ids) <= 1 \
            or bool(self._bf_retail_price_extra_value())

    def _bf_retail_write_shelf_price(self, price):
        """Write the price the shelf shows, without moving a sister variant.

        🔴 `lst_price` writes `list_price` on the TEMPLATE, which every variant of the model
        shares: with two variants in one batch, the second line rewrote the price of the first,
        and the shelf carried a label the till contradicted.
        """
        self.ensure_one()
        template = self.product_tmpl_id
        if len(template.product_variant_ids) <= 1:
            # One variant: the inverse of lst_price takes its extra out for us.
            self.lst_price = price
            return
        value = self._bf_retail_price_extra_value()
        value.ensure_one()
        shared = self.product_template_attribute_value_ids - value
        extra = price - template.list_price - sum(shared.mapped("price_extra"))
        # Under sudo, and only here: the retail manager writes the products and the categories
        #, and the price of a variant lives in the extra of an
        # attribute value by OUR OWN design. The right that answers
        # for this write is the one on the shelf price: `action_apply` asks for the manager
        # group before a single price is written.
        value.sudo().price_extra = extra

    def action_bf_retail_costs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("Cost series"),
            "res_model": "bf.retail.cost", "view_mode": "list,form",
            "domain": [("product_id", "=", self.id), ("company_id", "=", self.env.company.id)],
            "context": {"default_product_id": self.id, "default_uom_id": self.uom_id.id,
                        "search_default_group_by_none": 1},
        }


class ProductTemplate(models.Model):
    """What the single variant of a template shows, as Odoo does for the standard price. A
    template with several variants shows nothing: the series belongs to the variant."""
    _inherit = "product.template"

    bf_retail_managed = fields.Boolean(
        "Followed at retail", compute="_compute_bf_retail_managed", store=True)
    bf_retail_cost = fields.Float(
        "Retail cost", digits=(16, 4), compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_cost_date = fields.Date(
        "Cost of", compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_cost_stale = fields.Boolean(
        "Cost is old", compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_margin_pct = fields.Float(
        "Margin (%)", digits=(16, 1), compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_target_pct = fields.Float(
        "Target margin (%)", digits=(16, 1), compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_price_suggested = fields.Monetary(
        "Suggested price", currency_field="currency_id", compute="_compute_bf_retail_template",
        groups=MANAGER)
    bf_retail_price_gap = fields.Monetary(
        "Gap", currency_field="currency_id", compute="_compute_bf_retail_template", groups=MANAGER)
    bf_retail_cost_count = fields.Integer(
        "Costs observed", compute="_compute_bf_retail_template", groups=MANAGER)

    @api.depends("categ_id", "categ_id.bf_retail_managed", "categ_id.parent_path")
    def _compute_bf_retail_managed(self):
        for template in self:
            template.bf_retail_managed = template.categ_id._bf_retail_managed_here() \
                if template.categ_id else False

    @api.depends("product_variant_ids", "list_price", "taxes_id", "categ_id",
                 "categ_id.bf_retail_margin_target", "categ_id.bf_retail_price_rounding",
                 "product_variant_ids.bf_retail_cost_ids.cost",
                 "product_variant_ids.bf_retail_cost_ids.date",
                 "product_variant_ids.bf_retail_cost_ids.company_id")
    @api.depends_context("company", "uom", "tz")
    def _compute_bf_retail_template(self):
        for template in self:
            variants = template.product_variant_ids
            variant = variants if len(variants) == 1 else variants.browse()
            template.bf_retail_cost = variant.bf_retail_cost if variant else 0.0
            template.bf_retail_cost_date = variant.bf_retail_cost_date if variant else False
            template.bf_retail_cost_stale = variant.bf_retail_cost_stale if variant else False
            template.bf_retail_margin_pct = variant.bf_retail_margin_pct if variant else 0.0
            template.bf_retail_target_pct = variant.bf_retail_target_pct if variant else 0.0
            template.bf_retail_price_suggested = variant.bf_retail_price_suggested if variant else 0.0
            template.bf_retail_price_gap = variant.bf_retail_price_gap if variant else 0.0
            template.bf_retail_cost_count = variant.bf_retail_cost_count if variant else 0

    # ── the journal of shelf price moves ─────────────────────────
    def _bf_retail_shelf_prices(self):
        """{variant: the price its shelf shows} for the followed variants of these models.

        Read as the model's own price plus the variant's extra, and NOT through `lst_price`.
        🔴 `lst_price` on a variant is an INHERITED field: Odoo writes the new price into the
        VARIANT's cache, then delegates to this model's `write` -- so a before/after read of
        `lst_price` from inside that write sees the new price on both sides and journalises
        nothing at all. The model's own `list_price` is still the one from before at that
        moment, which is what makes this reading honest on both doors.
        """
        prices = {}
        for template in self:
            price = template.list_price
            for variant in template.product_variant_ids.filtered("bf_retail_managed"):
                prices[variant] = price + variant.price_extra
        return prices

    def _bf_retail_log_price_moves(self, before):
        """Write down every shelf price that moved since `before` was read.

        🔴 The index used to read the price changes the module had applied ITSELF, so a price
        moved by hand on the product read as "the store passed nothing": a mute note on the
        form and a number published without a mark. Every move of the shelf price of a followed
        product is journalised, from a batch or by hand, and the index reads the journal.
        """
        company = self.env.company
        rounding = company.currency_id.rounding or 0.01
        line_id = self.env.context.get("bf_retail_price_line")
        # The day of the batch while a batch is applying (a batch carries its own date), today
        # for a price typed on the product.
        day = self.env.context.get("bf_retail_price_date") or fields.Date.context_today(self)
        after = self._bf_retail_shelf_prices()
        vals_list = []
        for variant, old in before.items():
            new = after.get(variant)
            if new is None:
                continue
            if float_compare(new, old, precision_rounding=rounding) == 0:
                continue
            vals_list.append({
                "product_id": variant.id,
                "company_id": company.id,
                "date": day,
                "price_old": old,
                "price_new": new,
                "source": "batch" if line_id else "hand",
                "change_line_id": line_id or False,
                "user_id": self.env.user.id,
            })
        if vals_list:
            # Under sudo: a price can be moved by a purchase or an inventory manager who is not
            # in the retail group at all, and the journal has to hold what they did too.
            self.env["bf.retail.price.move"].sudo().create(vals_list)

    def write(self, vals):
        """The shelf price of a model, journalised whoever writes it.

        `list_price` is the model's price, and `lst_price` on a variant writes it back here
        through the inverse: this is the one door a price typed by hand goes through.
        """
        if "list_price" not in vals:
            return super().write(vals)
        before = self._bf_retail_shelf_prices()
        res = super().write(vals)
        self._bf_retail_log_price_moves(before)
        return res

    def action_bf_retail_costs(self):
        self.ensure_one()
        variants = self.product_variant_ids
        if len(variants) == 1:
            return variants.action_bf_retail_costs()
        return {
            "type": "ir.actions.act_window", "name": self.env._("Cost series"),
            "res_model": "bf.retail.cost", "view_mode": "list,form",
            "domain": [("product_id", "in", variants.ids),
                       ("company_id", "=", self.env.company.id)],
        }


class ProductTemplateAttributeValue(models.Model):
    """The other door a shelf price goes through: the price of a variant is written by its own
    extra, and that move belongs in the journal too."""
    _inherit = "product.template.attribute.value"

    def write(self, vals):
        if "price_extra" not in vals:
            return super().write(vals)
        templates = self.product_tmpl_id
        before = templates._bf_retail_shelf_prices()
        res = super().write(vals)
        templates._bf_retail_log_price_moves(before)
        return res
