from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .color_utils import normalize_hex


class BfColorOverride(models.Model):
    """A color chosen for one record by one user, or by the whole company."""

    _name = "bf.color.override"
    _description = "Color override"
    _order = "res_model, res_id"

    user_id = fields.Many2one("res.users", ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", ondelete="cascade", index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True, index=True)
    color_hex = fields.Char(string="Color", required=True)

    _sql_constraints = [
        (
            "one_owner",
            "CHECK((user_id IS NULL) != (company_id IS NULL))",
            "A color override belongs to a user or to a company, not both.",
        ),
    ]

    def init(self):
        # Two partial unique indexes: NULL never collides in a plain UNIQUE.
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS bf_color_override_user_uniq
                ON bf_color_override (user_id, res_model, res_id) WHERE user_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS bf_color_override_company_uniq
                ON bf_color_override (company_id, res_model, res_id) WHERE company_id IS NOT NULL;
            """
        )

    @api.constrains("color_hex")
    def _check_color_hex(self):
        for override in self:
            if not normalize_hex(override.color_hex):
                raise ValidationError(_("%s is not a hex color.", override.color_hex))

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
