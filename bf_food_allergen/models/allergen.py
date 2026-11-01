from odoo import _, fields, models
from odoo.exceptions import UserError


class FoodAllergen(models.Model):
    _name = "bf.food.allergen"
    _description = "Food allergen"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Char(translate=True, help="What the allergen covers, for the menu sheet.")

    _sql_constraints = [
        ("name_unique", "unique(name)", "This allergen already exists."),
    ]

    def _bf_allergen_uses(self):
        """{label: count} of what still declares these allergens. Each module that declares allergens
        adds its own (kitchen items, students, school meals), archived records included."""
        return {}

    def _bf_check_unused(self):
        """An allergen still declared stays: archived or deleted, the declaration would no longer be
        read, and a dish or a meal would no longer show it, or be refused to an allergic person."""
        uses = {label: n for label, n in self._bf_allergen_uses().items() if n}
        if uses:
            raise UserError(_("%(names)s is still declared (%(uses)s): the allergen stays active while it is "
                              "declared. Take it off first.", names=", ".join(self.mapped("name")),
                              uses=", ".join("%s %s" % (n, label) for label, n in uses.items())))

    def write(self, vals):
        if "active" in vals and not vals["active"]:
            self._bf_check_unused()
        return super().write(vals)

    def unlink(self):
        self._bf_check_unused()
        return super().unlink()
