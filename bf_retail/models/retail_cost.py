# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

SOURCES = [
    ("bill", "Vendor bill"),
    ("file", "Price file"),
    ("manual", "Entered by hand"),
    ("init", "Opening cost"),
]

# Written by the chain, never by hand: the change against the previous cost is a reading of
# the series, not a value someone gets to set.
ENGINE_FIELDS = ("previous_cost", "delta", "delta_pct", "has_previous")


class RetailCost(models.Model):
    """One cost observed for one product, in one company, on one day.

    The series is the point. A single "current cost" field cannot say whether a product went
    up 4 % twice this month, and that is exactly what a store needs to see before it moves a
    shelf price.
    """
    _name = "bf.retail.cost"
    _description = "Retail cost observed"
    _order = "date desc, id desc"
    _check_company_auto = True

    product_id = fields.Many2one(
        "product.product", "Product", required=True, index=True, ondelete="cascade",
        check_company=True)
    company_id = fields.Many2one(
        "res.company", "Company", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")
    date = fields.Date("On", required=True, index=True, default=fields.Date.context_today)
    # Four decimals: a case of 24 at 31.00 gives 1.2917 per unit, and two decimals would cost
    # 0.08 on the case. Not six, as in a kitchen: a retail unit is a can, not a gram.
    cost = fields.Float("Cost per unit", digits=(16, 4), required=True)
    uom_id = fields.Many2one("uom.uom", "Per", help="The product's own unit of measure.")
    source = fields.Selection(SOURCES, "From", required=True, default="manual")
    partner_id = fields.Many2one("res.partner", "Supplier", index=True)
    # 🔴 `cascade`, not `set null`: with the key nulled, a line deleted on a correction left
    # its cost orphaned in the series -- the bill cleaned up the rows of the lines it no longer
    # carried, and a deleted line is in no list at all. The series kept 12.00 and the suggested
    # price came out of it. `account.move.line.unlink` re-chains what is left.
    move_line_id = fields.Many2one(
        "account.move.line", "Bill line", ondelete="cascade", index=True,
        help="The line of the validated vendor bill this cost was read from.")
    move_id = fields.Many2one(related="move_line_id.move_id", string="Bill", store=True)
    note = fields.Char("Note")

    has_previous = fields.Boolean("Has a previous cost", readonly=True)
    previous_cost = fields.Float("Previous cost", digits=(16, 4), readonly=True)
    delta = fields.Float("Change", digits=(16, 4), readonly=True)
    delta_pct = fields.Float("Change (%)", digits=(16, 2), readonly=True)

    _sql_constraints = [
        ("cost_positive", "CHECK (cost >= 0)", "A cost cannot be negative."),
        # One cost per bill line: a bill reset to draft and posted again does not double the
        # series. NULL is not unique in Postgres, so hand entries are unaffected.
        ("one_per_bill_line", "UNIQUE (move_line_id)",
         "This bill line already wrote a cost in the series."),
    ]

    @api.depends("product_id", "date")
    def _compute_display_name(self):
        for row in self:
            row.display_name = "%s, %s" % (row.product_id.display_name or "", row.date or "")

    # ── the chain ────────────────────────────────────────────────────────────────────────
    def _bf_keys(self):
        return {(row.product_id.id, row.company_id.id) for row in self}

    @api.model
    def _bf_rechain(self, keys):
        """Rewrite the change of every row of these (product, company) pairs, in date order.

        A row inserted with an older date (a late bill, a price file read a week after) moves
        the ground under the rows that follow it: the whole series of that product is rewritten
        rather than the one row that was touched.
        """
        for product_id, company_id in keys:
            if not product_id or not company_id:
                continue
            rows = self.sudo().search(
                [("product_id", "=", product_id), ("company_id", "=", company_id)],
                order="date asc, id asc")
            previous = None
            for row in rows:
                if previous is None:
                    vals = {"has_previous": False, "previous_cost": 0.0, "delta": 0.0,
                            "delta_pct": 0.0}
                else:
                    delta = row.cost - previous
                    vals = {"has_previous": True, "previous_cost": previous, "delta": delta,
                            "delta_pct": (delta / previous * 100.0) if previous else 0.0}
                # Only when something moved: a rewrite of the whole series on every insert
                # would touch write_date on rows that did not change.
                if any(abs(row[f] - vals[f]) > 1e-9 if isinstance(vals[f], float)
                       else row[f] != vals[f] for f in vals):
                    row._bf_write_engine(vals)
                previous = row.cost

    @api.model
    def _bf_per_product_uom(self, product, uom, cost):
        """A cost, read per unit of the PRODUCT's own unit of measure.

        🔴 The unit of a row used to be decorative on the hand path: a manager who read
        "12.00 the dozen" off an invoice and typed it as it is written got a cost of 12.00 the
        UNIT, a margin of −381.9 % and a suggested price of 17.99. The bill path already
        converts a case; the hand path converts the same way. A unit that does not measure the
        same thing as the product's is refused rather than converted: `_compute_price` hands
        back the number unchanged across categories, which would be the same silent lie.
        """
        if not product or not uom or uom == product.uom_id:
            return (uom or product.uom_id).id, cost
        if uom.category_id != product.uom_id.category_id:
            raise ValidationError(_(
                "%(uom)s does not measure the same thing as %(product_uom)s, the unit of "
                "%(product)s: a cost in that unit cannot be read per unit.",
                uom=uom.display_name, product_uom=product.uom_id.display_name,
                product=product.display_name))
        return product.uom_id.id, uom._compute_price(cost, product.uom_id)

    def _bf_retail_same(self, field, value):
        """True when writing `value` in `field` would change nothing on this row.

        A bill posted again writes the rows of its lines from the lines; the ones it leaves
        identical are not touched, so their write_date keeps saying when the SERIES moved.
        """
        self.ensure_one()
        current = self[field]
        if self._fields[field].type == "many2one":
            return (current.id or False) == (value or False)
        if self._fields[field].type == "float":
            return float_compare(current, value, precision_digits=4) == 0
        return current == value

    def _bf_write_engine(self, vals):
        """The only door the four fields of the chain are written through.

        🔴 Not a context flag: `with_context(bf_retail_engine=True).write(...)` is one call
        away from any client, it was accepted, and the forged variation survived in the base. A
        method whose name starts with an underscore is refused by the RPC dispatcher, so the
        chain keeps a door the client cannot push on.
        """
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for number, vals in enumerate(vals_list, start=1):
            product = self.env["product.product"].browse(vals.get("product_id") or [])
            if not vals.get("uom_id") and product:
                vals["uom_id"] = product.uom_id.id
            if vals.get("uom_id") and "cost" in vals:
                uom = self.env["uom.uom"].browse(vals["uom_id"])
                # A doubtful unit refuses the whole `create` of a batch, and the refusal says
                # WHICH line it is about: the reader of a price file writes its rows in one
                # call, and "that unit does not measure the same thing" on a batch of 300 lines
                # names a product without saying where to look.
                try:
                    vals["uom_id"], vals["cost"] = self._bf_per_product_uom(
                        product, uom, vals["cost"])
                except ValidationError as refused:
                    if len(vals_list) == 1:
                        raise
                    raise ValidationError(_(
                        "Line %(number)s of %(total)s: %(reason)s", number=number,
                        total=len(vals_list), reason=refused.args[0])) from refused
        rows = super().create(vals_list)
        self._bf_rechain(rows._bf_keys())
        return rows

    def write(self, vals):
        if set(vals) & set(ENGINE_FIELDS):
            raise AccessError(_("The change against the previous cost is read from the series, "
                                "it is not written by hand."))
        if {"uom_id", "cost", "product_id"} & set(vals) and self:
            if len(self) > 1:
                # One conversion per row: a single write cannot carry two units.
                for row in self:
                    row.write(vals)
                return True
            product = self.env["product.product"].browse(vals["product_id"]) \
                if "product_id" in vals else self.product_id
            uom = self.env["uom.uom"].browse(vals["uom_id"]) \
                if vals.get("uom_id") else self.uom_id
            vals = dict(vals)
            vals["uom_id"], vals["cost"] = self._bf_per_product_uom(
                product, uom, vals.get("cost", self.cost))
        keys = self._bf_keys()
        res = super().write(vals)
        if {"cost", "date", "product_id", "company_id"} & set(vals):
            self._bf_rechain(keys | self._bf_keys())
        return res

    def unlink(self):
        keys = self._bf_keys()
        res = super().unlink()
        self._bf_rechain(keys)
        return res

    # ── reading the series ───────────────────────────────────────────────────────────────
    @api.model
    def _bf_last(self, product, company, upto=None):
        """The last cost of this product in this company, on or before `upto` -- today when
        the caller names no day.

        🔴 A cost dated in the FUTURE used to become the current cost for ever, and it never
        got old (`(today - date).days` is negative). A price file read in advance legitimately
        writes a cost dated the day the list comes into force (D9 of lot 2), so the reading is
        what must not take a row that is not in force yet for today's cost. The row stays in
        the series and becomes the current cost on its own day.
        """
        domain = [("product_id", "=", product.id), ("company_id", "=", company.id)]
        domain.append(("date", "<=", upto or fields.Date.context_today(self)))
        return self.sudo().search(domain, order="date desc, id desc", limit=1)

    def action_open_bill(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("This cost did not come from a bill."))
        return {"type": "ir.actions.act_window", "res_model": "account.move",
                "res_id": self.move_id.id, "view_mode": "form", "target": "current"}
