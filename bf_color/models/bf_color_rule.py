from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .color_utils import normalize_hex, stable_pick

CRITERION_TYPES = ["many2one", "char", "selection", "integer", "boolean"]


def value_key(value):
    """Key a record's criterion value the same way rule lines are keyed."""
    if isinstance(value, models.BaseModel):
        return str(value.id) if value else False
    if value is False or value is None or value == "":
        return False
    return str(value)


class BfColorRule(models.Model):
    """Color records of a model automatically, from the value of one of their fields.

    Clinic example: model ``bf.shift.assignment``, criterion ``employee_id``,
    one line per doctor. Values without a line take a stable color from the
    fallback swatch, so a new doctor gets a color nobody had to pick.
    """

    _name = "bf.color.rule"
    _description = "Automatic color rule"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", ondelete="cascade", index=True,
        default=lambda self: self.env.company,
        help="Leave empty to apply the rule to every company.",
    )
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain=[("transient", "=", False)],
    )
    model_name = fields.Char(string="Model name", related="model_id.model", store=True, index=True)
    field_id = fields.Many2one(
        "ir.model.fields", string="Criterion", required=True, ondelete="cascade",
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', 'in', %s)]"
        % CRITERION_TYPES,
    )
    line_ids = fields.One2many("bf.color.rule.line", "rule_id", string="Colors", copy=True)
    fallback_swatch_id = fields.Many2one(
        "bf.color.swatch", string="Fallback swatch", ondelete="set null",
        help="Values without a line get a stable color from this swatch.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        self.env.invalidate_all()
        return rules

    def write(self, vals):
        result = super().write(vals)
        self.env.invalidate_all()
        return result

    def unlink(self):
        result = super().unlink()
        self.env.invalidate_all()
        return result

    @api.constrains("model_id", "field_id")
    def _check_field(self):
        for rule in self:
            if rule.field_id.model_id != rule.model_id:
                raise ValidationError(_("The criterion must be a field of %s.", rule.model_id.name))
            if rule.field_id.ttype not in CRITERION_TYPES:
                raise ValidationError(_("This field type cannot be used as a criterion."))

    @api.model
    def _bf_colors_for(self, records):
        """``{record id: hex}`` for the records an active rule colors."""
        rules = self.sudo().search([
            ("model_name", "=", records._name),
            ("company_id", "in", [False] + self.env.companies.ids),
        ])
        result = {}
        for rule in rules:
            pending = records.filtered(lambda r: r.id not in result)
            if not pending:
                break
            result.update(rule._colors_for(pending))
        return result

    def _colors_for(self, records):
        self.ensure_one()
        table = {line.value_key: line.color_hex for line in self.line_ids if line.value_key}
        fallback = self.fallback_swatch_id.colors() if self.fallback_swatch_id else []
        result = {}
        for record in records:
            key = value_key(record[self.field_id.name])
            if not key:
                continue
            color = table.get(key) or stable_pick("%s:%s" % (self.id, key), fallback)
            if color:
                result[record.id] = color
        return result


class BfColorRuleLine(models.Model):
    _name = "bf.color.rule.line"
    _description = "Automatic color rule value"
    _order = "sequence, id"

    rule_id = fields.Many2one("bf.color.rule", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    field_type = fields.Selection(related="rule_id.field_id.ttype")
    value_model = fields.Char(related="rule_id.field_id.relation", store=True)
    value_res_id = fields.Many2oneReference(
        string="Record", model_field="value_model",
        help="For a relational criterion: the record that takes this color.",
    )
    value_char = fields.Char(
        string="Value",
        help="For a text, selection, number or yes/no criterion: the raw value.",
    )
    value_key = fields.Char(compute="_compute_value_key", store=True, index=True)
    color_hex = fields.Char(string="Color", required=True)

    @api.depends("field_type", "value_res_id", "value_char")
    def _compute_value_key(self):
        for line in self:
            if line.field_type == "many2one":
                line.value_key = str(line.value_res_id) if line.value_res_id else False
            else:
                line.value_key = (line.value_char or "").strip() or False

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
