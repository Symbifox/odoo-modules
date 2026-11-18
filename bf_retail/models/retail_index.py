# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class RetailIndex(models.Model):
    """How much the cost moved, how much the shelf price moved, and the spread between them.

    A store that passes its cost increases on late pays them itself. The two indices side by
    side are the only honest way to see it, and the spread is what a public index (the
    Infometrics and Foodstuffs grocery supplier cost index in New Zealand) compares against.
    """
    _name = "bf.retail.index"
    _description = "Retail cost against price index"
    _order = "date_to desc, id desc"
    _check_company_auto = True

    # Not stored: a name built with a translation and written to the base is frozen in the
    # language of whoever computed it, and read by everyone else in that language.
    name = fields.Char("Reference", compute="_compute_name")
    company_id = fields.Many2one(
        "res.company", "Company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date("From", required=True, default=lambda self: fields.Date.add(
        fields.Date.context_today(self), months=-3))
    date_to = fields.Date("To", required=True, default=fields.Date.context_today)
    computed = fields.Boolean("Computed", readonly=True, copy=False)
    line_ids = fields.One2many("bf.retail.index.line", "index_id", "Categories", readonly=True)
    product_count = fields.Integer("Products counted", compute="_compute_totals")
    cost_index = fields.Float("Cost index", digits=(16, 1), compute="_compute_totals",
                              help="100 means the cost did not move over the period.")
    price_index = fields.Float("Price index", digits=(16, 1), compute="_compute_totals")
    spread = fields.Float("Spread", digits=(16, 1), compute="_compute_totals",
                          help="Price index less cost index. Below zero, the store is paying "
                               "the increase itself.")

    @api.depends("date_from", "date_to", "company_id")
    def _compute_name(self):
        for index in self:
            index.name = self.env._("Cost against price, %(start)s to %(end)s",
                                    start=index.date_from or "", end=index.date_to or "")

    @api.depends("line_ids.cost_index", "line_ids.price_index", "line_ids.product_count")
    def _compute_totals(self):
        for index in self:
            lines = index.line_ids
            total = sum(lines.mapped("product_count"))
            index.product_count = total
            # Weighted by the number of products of each category, not by sales: what sells
            # is not in this module yet, and an unweighted average of categories would let a
            # category of three products move the whole index.
            if total:
                index.cost_index = sum(l.cost_index * l.product_count for l in lines) / total
                index.price_index = sum(l.price_index * l.product_count for l in lines) / total
            else:
                index.cost_index = index.price_index = 0.0
            index.spread = index.price_index - index.cost_index

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for index in self:
            # A single day is a period: a cost at both ends of it reads 100, which is an
            # answer. Only a period that ends before it starts is refused.
            if index.date_from and index.date_to and index.date_from > index.date_to:
                raise ValidationError(_("The period starts after it ends."))

    def _bf_price_at(self, product, date):
        """The shelf price of this product on `date`, read backwards from today.

        🔴 Read from the price changes the module had APPLIED, this answer left out every
        price a manager typed on the product: a rise passed by hand read as "the store passed
        nothing". Every move of the shelf price of a followed product is journalised now, by a
        batch or by hand, and this reads the journal. The price at the
        end of a past day is the "price before" of the earliest move AFTER that day, and
        today's price when nothing moved since.
        """
        move = self.env["bf.retail.price.move"].sudo().search(
            [("product_id", "=", product.id), ("company_id", "=", self.company_id.id),
             ("date", ">", date)],
            order="date asc, id asc", limit=1)
        return move.price_old if move else product.lst_price

    def action_compute(self):
        self.ensure_one()
        company = self.company_id
        Cost = self.env["bf.retail.cost"]
        products = self.env["product.product"].with_company(company).search(
            [("bf_retail_managed", "=", True), ("active", "=", True),
             ("company_id", "in", (company.id, False))])
        rows = {}
        for product in products:
            start = Cost._bf_last(product, company, upto=self.date_from)
            end = Cost._bf_last(product, company, upto=self.date_to)
            # Both ends needed: a product first costed in the middle of the period would show
            # an infinite increase, which is an artefact of when it arrived in the store.
            if not start or not end or not start.cost or not end.cost:
                continue
            price_start = self._bf_price_at(product, self.date_from)
            # 🔴 At `date_to`, not today: the two ends of the cost respect the period, and a
            # price read at today's value turned a spread of −10.0 into +30.0 -- the module
            # said the store had passed MORE than the increase where it had passed 10 points
            # less. For a period that ends today the two readings are the same number.
            price_end = self._bf_price_at(product, self.date_to)
            if not price_start or not price_end:
                continue
            untaxed_start = product._bf_retail_untaxed(price_start, company)
            untaxed_end = product._bf_retail_untaxed(price_end, company)
            bucket = rows.setdefault(product.categ_id.id, {
                "n": 0, "cs": 0.0, "ce": 0.0, "ps": 0.0, "pe": 0.0, "ms": 0.0, "me": 0.0})
            bucket["n"] += 1
            bucket["cs"] += start.cost
            bucket["ce"] += end.cost
            bucket["ps"] += price_start
            bucket["pe"] += price_end
            bucket["ms"] += ((untaxed_start - start.cost) / untaxed_start * 100.0) \
                if untaxed_start else 0.0
            bucket["me"] += ((untaxed_end - end.cost) / untaxed_end * 100.0) \
                if untaxed_end else 0.0
        self.line_ids.unlink()
        lines = []
        for categ_id, b in rows.items():
            n = b["n"]
            lines.append(fields.Command.create({
                "categ_id": categ_id,
                "product_count": n,
                "cost_start": b["cs"] / n, "cost_end": b["ce"] / n,
                "price_start": b["ps"] / n, "price_end": b["pe"] / n,
                "margin_start": b["ms"] / n, "margin_end": b["me"] / n,
            }))
        self.write({"line_ids": lines, "computed": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "current", "name": _("Cost against price")}


class RetailIndexLine(models.Model):
    _name = "bf.retail.index.line"
    _description = "Retail index, one category"
    _order = "spread, id"

    index_id = fields.Many2one(
        "bf.retail.index", "Index", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="index_id.company_id", store=True)
    currency_id = fields.Many2one(related="index_id.currency_id")
    categ_id = fields.Many2one("product.category", "Category", required=True)
    product_count = fields.Integer("Products", readonly=True)
    cost_start = fields.Float("Cost at the start", digits=(16, 4), readonly=True)
    cost_end = fields.Float("Cost at the end", digits=(16, 4), readonly=True)
    price_start = fields.Monetary("Price at the start", currency_field="currency_id", readonly=True)
    price_end = fields.Monetary("Price at the end", currency_field="currency_id", readonly=True)
    margin_start = fields.Float("Margin at the start (%)", digits=(16, 1), readonly=True)
    margin_end = fields.Float("Margin at the end (%)", digits=(16, 1), readonly=True)
    cost_index = fields.Float("Cost index", digits=(16, 1), compute="_compute_indices", store=True)
    price_index = fields.Float("Price index", digits=(16, 1), compute="_compute_indices",
                               store=True)
    spread = fields.Float("Spread", digits=(16, 1), compute="_compute_indices", store=True)

    @api.depends("cost_start", "cost_end", "price_start", "price_end")
    def _compute_indices(self):
        for line in self:
            line.cost_index = (line.cost_end / line.cost_start * 100.0) if line.cost_start else 0.0
            line.price_index = (line.price_end / line.price_start * 100.0) \
                if line.price_start else 0.0
            line.spread = line.price_index - line.cost_index
