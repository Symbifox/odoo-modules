from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .color_utils import normalize_hex


class BfColorSwatch(models.Model):
    """A saved set of colors, personal (user) or shared with the company."""

    _name = "bf.color.swatch"
    _description = "Color swatch"
    _order = "sequence, name, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", ondelete="cascade", index=True,
        help="Set for a personal swatch. Leave empty to share it with the company.",
    )
    company_id = fields.Many2one(
        "res.company", ondelete="cascade", index=True,
        default=lambda self: self.env.company,
    )
    line_ids = fields.One2many("bf.color.swatch.line", "swatch_id", string="Colors", copy=True)
    shared = fields.Boolean(compute="_compute_shared", store=True)

    @api.depends("user_id")
    def _compute_shared(self):
        for swatch in self:
            swatch.shared = not swatch.user_id

    @api.constrains("user_id", "company_id")
    def _check_owner(self):
        for swatch in self:
            if not swatch.user_id and not swatch.company_id:
                raise ValidationError(_("A shared swatch needs a company."))

    def colors(self):
        self.ensure_one()
        return self.line_ids.sorted("sequence").mapped("color_hex")

    @api.model
    def bf_available(self):
        """Swatches the current user can pick from: their own, then the company's."""
        swatches = self.search([
            "|",
            ("user_id", "=", self.env.uid),
            "&", ("user_id", "=", False), ("company_id", "in", self.env.companies.ids),
        ])
        return [
            {
                "id": swatch.id,
                "name": swatch.name,
                "shared": swatch.shared,
                "colors": swatch.colors(),
            }
            for swatch in swatches.sorted(lambda s: (s.shared, s.sequence, s.name or ""))
        ]

    @api.model
    def bf_add_to_mine(self, color):
        """Append a color to the current user's first personal swatch, created if needed."""
        value = normalize_hex(color)
        if not value:
            raise ValidationError(_("%s is not a hex color.", color))
        swatch = self.search([("user_id", "=", self.env.uid)], limit=1)
        if not swatch:
            swatch = self.create({"name": _("My colors"), "user_id": self.env.uid})
        if value not in swatch.colors():
            last = max(swatch.line_ids.mapped("sequence") or [0])
            swatch.write({"line_ids": [(0, 0, {"color_hex": value, "sequence": last + 1})]})
        return swatch.id


class BfColorSwatchLine(models.Model):
    _name = "bf.color.swatch.line"
    _description = "Color swatch entry"
    _order = "sequence, id"

    swatch_id = fields.Many2one("bf.color.swatch", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char()
    color_hex = fields.Char(string="Color", required=True)

    @api.constrains("color_hex")
    def _check_color_hex(self):
        for line in self:
            if not normalize_hex(line.color_hex):
                raise ValidationError(_("%s is not a hex color.", line.color_hex))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("color_hex"):
                vals["color_hex"] = normalize_hex(vals["color_hex"]) or vals["color_hex"]
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("color_hex"):
            vals["color_hex"] = normalize_hex(vals["color_hex"]) or vals["color_hex"]
        return super().write(vals)
