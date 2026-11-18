# -*- coding: utf-8 -*-
"""The position: one product on one shelf, and the three numbers that make a capacity.

Facings is what the customer sees from the aisle, deep is what stands behind each facing,
stacked is what is piled on top. Capacity is the three multiplied, and it is the number the
rest of the suite waits for: days of supply at the shelf is capacity over daily velocity,
and neither is computable without a position.

🔴 The width taken comes from the PRODUCT, never typed here. A product with no width takes
zero, which would quietly report a full shelf as half empty, so a position whose product has
no dimensions is not silently treated as fitting: `measured` is false, and the count of
unmeasured positions is carried up to the shelf and to the fixture, where the drawing and
the PDF both say so.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class BfPlanogramPosition(models.Model):
    _name = "bf.planogram.position"
    _description = "Planogram position"
    _order = "shelf_id, sequence, id"

    shelf_id = fields.Many2one(
        "bf.planogram.shelf", string="Shelf", required=True, index=True, ondelete="cascade")
    # Stored, because the position is what lot 4 reads and what a round counts: it must be
    # groupable by fixture and filterable by company without walking the shelf every time.
    fixture_id = fields.Many2one(
        related="shelf_id.fixture_id", store=True, index=True, string="Fixture")
    company_id = fields.Many2one(
        related="shelf_id.company_id", store=True, index=True, string="Company")

    # restrict, not cascade: a product on a shelf does not vanish from the planogram because
    # someone deleted it in the catalogue. A product that leaves the store is archived, and
    # an archived product keeps its position until the position is taken off the shelf.
    product_id = fields.Many2one(
        "product.product", string="Product", required=True, index=True, ondelete="restrict",
        domain="[('type', '=', 'consu')]")
    sequence = fields.Integer(string="Order", default=10,
                              help="Left to right along the shelf.")

    facings = fields.Integer(string="Facings", default=1, required=True,
                             help="What the aisle sees, side by side.")
    deep = fields.Integer(string="Deep", default=1, required=True,
                          help="How many stand behind each facing.")
    stacked = fields.Integer(string="Stacked", default=1, required=True,
                             help="How many are piled on top of each other.")

    unit_width = fields.Float(related="product_id.bf_planogram_unit_width", string="Unit width (cm)")
    unit_height = fields.Float(related="product_id.bf_planogram_unit_height", string="Unit height (cm)")
    unit_depth = fields.Float(related="product_id.bf_planogram_unit_depth", string="Unit depth (cm)")

    # 🔴 What the product measured when the version was frozen. `measured`, `used_width`
    # and `capacity` are STORED and depend on the product sheet: their recompute does not go
    # through `write`, so the freeze guard never sees it, and a frozen version moved the
    # day someone corrected a product. On a frozen version the computation
    # reads these copies instead of the product; they are written once, by the freeze,
    # before the version is closed.
    frozen_unit_width = fields.Float(string="Width when frozen (cm)", readonly=True,
                                     copy=False)
    frozen_unit_height = fields.Float(string="Height when frozen (cm)", readonly=True,
                                      copy=False)
    # 🔴 And what the product was CALLED that day.
    # A version is the evidence a compliance round is counted against; the
    # plan reprinted from it read the product sheet of TODAY, so renaming or recoding a
    # product rewrote every frozen plan it appeared on. Same pattern as the two widths
    # above: written once by the freeze, before the version is closed, read by the drawing.
    frozen_product_name = fields.Char(string="Product when frozen", readonly=True,
                                      copy=False)
    frozen_product_code = fields.Char(string="Reference when frozen", readonly=True,
                                      copy=False)
    # What the views read to know which of the two to show: `unit_width` is a LIVE related of
    # the product sheet, and the sheet keeps changing after the freeze. The form and the list
    # showed the live one on a frozen version, beside numbers computed from the copy above
    # (forme restée du ).
    fixture_locked = fields.Boolean(related="fixture_id.locked", string="Frozen version")

    measured = fields.Boolean(compute="_compute_space", store=True, string="Measured",
                              help="The product carries a width, so the space it takes is "
                                   "read and not guessed.")
    used_width = fields.Float(compute="_compute_space", store=True, string="Taken (cm)",
                              digits=(16, 1))
    capacity = fields.Integer(compute="_compute_space", store=True, string="Capacity (units)")

    @api.depends("facings", "deep", "stacked", "product_id.bf_planogram_unit_width")
    def _compute_space(self):
        for position in self:
            width = position._drawn_unit_width()
            position.measured = width > 0.0
            position.used_width = float_round(width * max(position.facings, 0), 1)
            position.capacity = (max(position.facings, 0) * max(position.deep, 0)
                                 * max(position.stacked, 0))

    def _drawn_unit_width(self):
        """The product's width, or the one it had when the version was frozen."""
        self.ensure_one()
        if self.fixture_id.locked:
            return self.frozen_unit_width or 0.0
        return self.product_id.bf_planogram_unit_width or 0.0

    def _drawn_product_name(self):
        """What the drawing and the PDF write on the box, and in the table under it."""
        self.ensure_one()
        if self._carries_the_labels_of_the_freeze():
            return self.frozen_product_name
        return self.product_id.display_name or ""

    def _drawn_product_code(self):
        self.ensure_one()
        if self._carries_the_labels_of_the_freeze():
            # Read whole, empty included: a product with no reference at the freeze keeps
            # no reference on the plan, and showing the one added afterwards would be the
            # very thing n° 7 was about.
            return self.frozen_product_code or ""
        return self.product_id.default_code or ""

    def _carries_the_labels_of_the_freeze(self):
        """A version frozen BEFORE the copies existed carries none: it falls back to the
        product sheet, which is the best a plan printed from it can do. The name is the
        witness -- a code may legitimately be empty, a name may not."""
        self.ensure_one()
        return bool(self.fixture_id.locked and self.frozen_product_name)

    def _drawn_unit_height(self):
        """The product's height, or the one it had when the version was frozen."""
        self.ensure_one()
        if self.fixture_id.locked:
            return self.frozen_unit_height or 0.0
        return self.product_id.bf_planogram_unit_height or 0.0

    @api.depends("product_id", "facings")
    def _compute_display_name(self):
        for position in self:
            position.display_name = _(
                "%(product)s, %(facings)s facing(s)",
                product=position.product_id.display_name or "", facings=position.facings)

    @api.constrains("facings", "deep", "stacked")
    def _check_counts(self):
        for position in self:
            if position.facings < 1 or position.deep < 1 or position.stacked < 1:
                raise ValidationError(_(
                    "A position holds at least one facing, one deep and one stacked: a "
                    "product that is not on the shelf is taken off the shelf, not set to "
                    "zero."))

    @api.model_create_multi
    def create(self, vals_list):
        shelves = self.env["bf.planogram.shelf"].browse(
            [v["shelf_id"] for v in vals_list if v.get("shelf_id")])
        shelves.fixture_id._assert_open(_("A position"))
        return super().create(vals_list)

    def write(self, vals):
        self.shelf_id.fixture_id._assert_open(_("A position"))
        if vals.get("shelf_id"):
            self.env["bf.planogram.shelf"].browse(
                vals["shelf_id"]).fixture_id._assert_open(_("A position"))
        return super().write(vals)

    def unlink(self):
        self.shelf_id.fixture_id._assert_open(_("A position"))
        return super().unlink()
