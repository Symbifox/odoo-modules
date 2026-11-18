# -*- coding: utf-8 -*-
"""The dimensions of what is sold, which Odoo does not carry.

Odoo 18 knows a product's `volume` and its `weight`, and neither says how wide the box is.
A planogram without a width is a drawing of guesses: the width taken by a facing is the
width of the unit, and the capacity of a shelf is what the widths leave. So six fields, in
centimetres, in the module that needs them -- not in the retail suite, because a pharmacy, a
hardware store or a library measures its shelves without following a cost or a margin.

Three for the SELLING unit, which is what sits on the shelf, and three for the CASE, which is
what arrives from the supplier and what the back room is stacked with. They are not derived
from one another: a case of 12 is not 12 widths, it is a box.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# A unit wider than three metres is a typo, and it would stretch the drawing off the page.
DIM_MAX = 300.0

DIMENSIONS = (
    "bf_planogram_unit_width", "bf_planogram_unit_height", "bf_planogram_unit_depth",
    "bf_planogram_case_width", "bf_planogram_case_height", "bf_planogram_case_depth",
)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    bf_planogram_unit_width = fields.Float(
        string="Unit width (cm)", digits=(16, 1),
        help="Across the facing, as the product stands on the shelf. This is the one the "
             "space taken is read from.")
    bf_planogram_unit_height = fields.Float(string="Unit height (cm)", digits=(16, 1))
    bf_planogram_unit_depth = fields.Float(string="Unit depth (cm)", digits=(16, 1))
    bf_planogram_case_width = fields.Float(string="Case width (cm)", digits=(16, 1))
    bf_planogram_case_height = fields.Float(string="Case height (cm)", digits=(16, 1))
    bf_planogram_case_depth = fields.Float(string="Case depth (cm)", digits=(16, 1))

    bf_planogram_measured = fields.Boolean(
        compute="_compute_bf_planogram_measured", string="Measured for the shelf",
        search="_search_bf_planogram_measured")
    bf_planogram_position_count = fields.Integer(
        compute="_compute_bf_planogram_position_count", string="On the shelf")

    @api.depends("bf_planogram_unit_width")
    def _compute_bf_planogram_measured(self):
        for template in self:
            template.bf_planogram_measured = template.bf_planogram_unit_width > 0.0

    def _search_bf_planogram_measured(self, operator, value):
        if operator not in ("=", "!=") or not isinstance(value, bool):
            # 🔴 `UserError`, comme son jumeau `fixture.py` (forme, une
            # methode de RECHERCHE ne valide pas un enregistrement, elle refuse un domaine,
            # et une `ValidationError` s'y affiche comme un champ fautif qui n'existe pas.
            raise UserError(_("A product is measured for the shelf or it is not."))
        wanted = value if operator == "=" else not value
        return [("bf_planogram_unit_width", ">" if wanted else "<=", 0.0)]

    # 🔴 `@api.depends`, and the one2many below exists for it: without it the count was
    # computed ONCE per transaction and never invalidated, so a position added right after
    # reading the product left the counter at zero -- and « See the positions » is hidden
    # when the counter is zero, so the button stayed invisible on a product that sits on a
    # shelf.
    @api.depends("product_variant_ids.bf_planogram_position_ids")
    def _compute_bf_planogram_position_count(self):
        counts = {}
        if self.ids:
            rows = self.env["bf.planogram.position"]._read_group(
                [("product_id.product_tmpl_id", "in", self.ids)],
                groupby=["product_id"], aggregates=["__count"])
            for product, count in rows:
                counts[product.product_tmpl_id.id] = (
                    counts.get(product.product_tmpl_id.id, 0) + count)
        for template in self:
            template.bf_planogram_position_count = counts.get(template.id, 0)

    @api.constrains(*DIMENSIONS)
    def _check_bf_planogram_dimensions(self):
        for template in self:
            for name in DIMENSIONS:
                value = template[name]
                if value < 0:
                    raise ValidationError(_("A dimension is not negative."))
                if value > DIM_MAX:
                    raise ValidationError(_(
                        "A dimension of %(value)s cm is not a unit on a shelf: the field is "
                        "in centimetres, and nothing on a shelf is wider than %(max)s cm.",
                        value=value, max=int(DIM_MAX)))

    def action_bf_planogram_positions(self):
        """From the product, where it sits. The twin of « on the plan » of the floor plans."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("On the shelf"),
            "res_model": "bf.planogram.position",
            "domain": [("product_id.product_tmpl_id", "=", self.id)],
            "context": {"create": False},
            "view_mode": "list,form",
        }


class ProductProduct(models.Model):
    _inherit = "product.product"

    # The inverse side of the position, and the only reason it exists: it is what the count
    # on the template depends on. No view shows it.
    bf_planogram_position_ids = fields.One2many(
        "bf.planogram.position", "product_id", string="Shelf positions")
