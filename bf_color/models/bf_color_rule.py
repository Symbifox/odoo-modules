from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .color_utils import DEFAULT_PALETTE, least_used, normalize_hex, stable_pick

CRITERION_TYPES = ["many2one", "many2many", "char", "selection", "integer", "boolean"]
RELATIONAL_TYPES = ("many2one", "many2many")
# A value's color may itself come from a rule on the value's model (an employee
# colored by department). Past this depth the value's own color is not followed,
# which also stops two rules that point at each other.
MAX_VALUE_DEPTH = 3


def value_key(value):
    """Key a record's criterion value the same way rule lines are keyed."""
    if isinstance(value, models.BaseModel):
        return str(value.id) if value else False
    if value is False or value is None or value == "":
        return False
    return str(value)


class BfColorRule(models.Model):
    """Color records of a model automatically, from the value of one of their fields.

    Clinic example: model ``bf.shift.assignment``, criterion ``employee_id``.
    Each doctor's color lives on the doctor's own record and every agenda that
    sorts by doctor shows it; a line of the rule overrides it for this model
    only. With a fallback swatch, a new doctor is given the least used color of
    the swatch, written on the doctor's record so that it never moves.
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
        domain="[('bf_color_colorable', '=', True)]",
        help="Only models that carry a free color can be colored by a rule.",
    )
    model_name = fields.Char(string="Model name", related="model_id.model", store=True, index=True)
    field_id = fields.Many2one(
        "ir.model.fields", string="Criterion", required=True, ondelete="cascade",
        domain="[('model_id', '=', model_id), ('store', '=', True), ('ttype', 'in', %s)]"
        % CRITERION_TYPES,
    )
    field_type = fields.Selection(related="field_id.ttype")
    value_model = fields.Char(related="field_id.relation", store=True, index=True)
    value_colorable = fields.Boolean(
        compute="_compute_value_colorable",
        help="The criterion points at records that carry their own color.",
    )
    only_listed = fields.Boolean(
        string="Listed values only",
        help="Color only the records whose value is in the list below. Other "
             "records keep the color they would have without this rule.",
    )
    line_ids = fields.One2many("bf.color.rule.line", "rule_id", string="Colors", copy=True)
    fallback_swatch_id = fields.Many2one(
        "bf.color.swatch", string="Fallback swatch", ondelete="set null",
        help="Values with no color of their own get one from this swatch: the "
             "least used color, written on the value when it is created.",
    )

    @api.depends("value_model")
    def _compute_value_colorable(self):
        colorable = set(self.env["bf.color.mixin"]._bf_color_model_names())
        for rule in self:
            rule.value_colorable = rule.value_model in colorable

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
        colorable = set(self.env["bf.color.mixin"]._bf_color_model_names())
        for rule in self:
            if rule.model_id.model not in colorable:
                raise ValidationError(_(
                    "%s has no free color: a rule on it would color nothing.", rule.model_id.name
                ))
            if rule.field_id.model_id != rule.model_id:
                raise ValidationError(_("The criterion must be a field of %s.", rule.model_id.name))
            if rule.field_id.ttype not in CRITERION_TYPES:
                raise ValidationError(_("This field type cannot be used as a criterion."))

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @api.model
    def _bf_colors_for(self, records):
        """``{record id: hex}`` for the records an active rule colors."""
        rules = self.sudo().search([
            ("active", "=", True),
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

    def _palette(self):
        self.ensure_one()
        return (self.fallback_swatch_id.colors() if self.fallback_swatch_id else []) or []

    def _colors_for(self, records):
        self.ensure_one()
        name = self.field_id.name
        field = records._fields.get(name)
        if not field:
            return {}
        # Only what the current user may read: one private record in an agenda
        # must not take the color away from the others.
        records = records._filtered_access("read")
        try:
            records.mapped(name)
        except AccessError:
            # The criterion field itself is out of the user's reach.
            return {}
        table, order = {}, []
        for line in self.line_ids.sorted(lambda line: (line.sequence, line.id)):
            if line.value_key and line.value_key not in table:
                table[line.value_key] = line.color_hex
                order.append(line.value_key)
        relational = field.type in RELATIONAL_TYPES
        palette = self._palette()
        value_colors = {}
        depth = self.env.context.get("bf_color_depth", 0)
        if relational and not self.only_listed and self.value_colorable and depth < MAX_VALUE_DEPTH:
            # The value's color as the current user sees it (their own color
            # for Dr A first). Read in sudo, but only its OWN color and the
            # overrides on it: the value's rules would read in sudo too.
            values = records.mapped(name).sudo().with_context(
                bf_color_depth=depth + 1, bf_color_own_only=True)
            value_colors = {value.id: value.color_resolved for value in values}
        result = {}
        for record in records:
            value = record[name]
            if field.type == "many2many":
                keys = [str(value_id) for value_id in value.ids]
            else:
                key = value_key(value)
                keys = [key] if key else []
            if not keys:
                continue
            # The rule's own lines first, in their order: with several tags,
            # the highest line present on the record wins.
            color = next((table[key] for key in order if key in keys), False)
            if not color and not self.only_listed:
                if relational:
                    color = next((value_colors[int(key)] for key in keys if value_colors.get(int(key))), False)
                if not color and palette:
                    color = stable_pick("%s:%s" % (self.id, keys[0]), palette)
            if color:
                result[record.id] = color
        return result

    # ------------------------------------------------------------------
    # Giving values a color
    # ------------------------------------------------------------------

    def _missing_values(self, values):
        return values.filtered(lambda value: not value._bf_color_own())

    def _assign(self, values, palette):
        """Write the least used palette color on each value without a color of its own."""
        Model = self.env[values._name].sudo().with_context(active_test=False)
        # Counted in SQL: only free colors can come from the palette.
        used = []
        for color, count in Model._read_group([("color_hex", "!=", False)], ["color_hex"], ["__count"]):
            used.extend([color] * count)
        for value in values.sudo():
            color = least_used(palette, used)
            value.color_hex = color
            used.append(color)
        return len(values)

    @api.model
    def _bf_autoassign(self, values):
        """New values of a rule with a fallback swatch get a color at once."""
        if not values or "color_hex" not in values._fields:
            return
        rules = self.sudo().search([
            ("active", "=", True),
            ("value_model", "=", values._name),
            ("fallback_swatch_id", "!=", False),
            ("only_listed", "=", False),
            ("company_id", "in", [False] + self.env.companies.ids),
        ], limit=1)
        if rules:
            missing = rules._missing_values(values)
            if missing:
                rules._assign(missing, rules._palette())

    def action_assign_missing(self):
        """Give a color to every value in use that has none (button on the rule)."""
        self.ensure_one()
        # Public, so callable by RPC by anyone who reads the rule: it writes in
        # sudo, so it is reserved to those who may change the rule.
        self.check_access("write")
        if not self.value_colorable:
            raise UserError(_("The values of this criterion cannot carry a color of their own."))
        Target = self.env[self.model_name].sudo()
        domain = [(self.field_id.name, "!=", False)]
        if self.company_id and "company_id" in Target._fields:
            domain.append(("company_id", "in", [self.company_id.id, False]))
        values = Target.search(domain).mapped(self.field_id.name)
        missing = self._missing_values(values)
        count = self._assign(missing, self._palette() or DEFAULT_PALETTE) if missing else 0
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "info",
                "message": _("%s values received a color.", count) if count
                else _("Every value already has a color."),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


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
            if line.field_type in RELATIONAL_TYPES:
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
