from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .color_utils import ODOO_PALETTE, index_to_hex, nearest_index, normalize_hex, text_color


BF_COLOR_COMPUTED = ["color_resolved", "color_resolved_index", "color_text", "color_source"]


class BfColorMixin(models.AbstractModel):
    """Free color on a record, resolved for the current user.

    Resolution, strongest first:
      1. the current user's override for this record;
      2. the current company's override for this record;
      3. the first automatic rule that colors this record;
      4. the record's own ``color_hex``, or else the hex of its Odoo ``color`` index;
      5. no color.

    Models that already carry an Odoo ``color`` index keep it untouched: the
    mixin reads it as the record's own color when ``color_hex`` is empty.
    """

    _name = "bf.color.mixin"
    _description = "Resolved free color"

    color_hex = fields.Char(string="Own color", help="Color of the record for everyone (hex).")
    color_resolved = fields.Char(
        string="Displayed color", compute="_compute_color_resolved",
        help="Color shown to the current user, after personal, company and automatic rules.",
    )
    color_resolved_index = fields.Integer(
        string="Displayed color (Odoo palette)", compute="_compute_color_resolved",
        help="Closest Odoo palette index, for views that only understand 0-11.",
    )
    color_text = fields.Char(string="Text color", compute="_compute_color_resolved")
    color_source = fields.Selection(
        [
            ("user", "Mine"),
            ("company", "Company"),
            ("rule", "Automatic rule"),
            ("record", "Record"),
        ],
        compute="_compute_color_resolved",
    )

    @api.depends("color_hex")
    # The "own color only" flag is part of the cache key: otherwise a color
    # computed WITH the value's rules is served again where only its own color
    # may be followed (and the second-level leak is back).
    @api.depends_context("uid", "company", "bf_color_own_only")
    def _compute_color_resolved(self):
        real = self.filtered("id")
        overrides = self._bf_color_overrides(real)
        # A value followed by a rule (a doctor behind a shift) shows only its own
        # color and the overrides on it: its own rules ran in sudo there, and a
        # criterion the user cannot read must not leak through a color.
        own_only = self.env.context.get("bf_color_own_only")
        rules = self.env["bf.color.rule"]._bf_colors_for(real) if real and not own_only else {}
        for record in self:
            color, source = False, False
            rid = record.id if record in real else False
            for candidate, name in (
                (overrides["user"].get(rid), "user"),
                (overrides["company"].get(rid), "company"),
                (rules.get(rid), "rule"),
                (record._bf_color_own(), "record"),
            ):
                if candidate:
                    color, source = candidate, name
                    break
            record.color_resolved = color
            record.color_resolved_index = nearest_index(color, record._bf_color_palette()) if color else 0
            record.color_text = text_color(color)
            record.color_source = source

    def _bf_color_sync_index(self, vals):
        """Keep ``color_hex`` and Odoo's ``color`` index in step: the last one written wins.

        A new ``color_hex`` moves the index to the closest palette entry, for views
        that only read the index. A new index alone (Odoo's own color picker)
        clears ``color_hex``, which would otherwise hide it.
        """
        if vals.get("color_hex"):
            # Always a clean #RRGGBB: the value ends up in a style attribute.
            own = normalize_hex(vals["color_hex"])
            if not own:
                raise ValidationError(_("%s is not a hex color.", vals["color_hex"]))
            vals = dict(vals, color_hex=own)
        if not self._bf_color_has_index(stored=True):
            return vals
        if "color" in vals and "color_hex" not in vals:
            return dict(vals, color_hex=False)
        if "color_hex" not in vals or "color" in vals:
            return vals
        own = normalize_hex(vals["color_hex"])
        return dict(vals, color_hex=own or False, color=nearest_index(own, self._bf_color_palette()) if own else 0)

    @api.model
    def _bf_color_has_index(self, stored=False):
        """True when the model carries Odoo's integer ``color`` index.

        With ``stored``, only an index the model owns: a related index (a shift
        showing its template's color) must never be written through.
        """
        field = self._fields.get("color")
        if not field or field.type != "integer":
            return False
        return not stored or (field.store and not field.related)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create([self._bf_color_sync_index(vals) for vals in vals_list])
        self.env["bf.color.rule"].sudo()._bf_autoassign(records)
        return records

    def write(self, vals):
        return super().write(self._bf_color_sync_index(vals))

    def _bf_color_own(self):
        own = normalize_hex(self.color_hex)
        if own:
            return own
        if self._bf_color_has_index():
            return index_to_hex(self.color, self._bf_color_palette())
        return False

    @api.model
    def _bf_color_model_names(self):
        """Every concrete model wired to the mixin, the only ones a rule can color."""
        mixin = self.env.registry["bf.color.mixin"]
        return sorted(
            name for name, cls in self.env.registry.items()
            if name != mixin._name and not cls._abstract and not cls._transient
            and issubclass(cls, mixin)
        )

    @api.model
    def _bf_color_palette(self):
        """Hex of each ``color`` index, 0 being "no color". Override for a model's own palette."""
        return ODOO_PALETTE

    @api.model
    def _bf_color_overrides(self, records):
        found = {"user": {}, "company": {}}
        if not records:
            return found
        rows = self.env["bf.color.override"].sudo().search_read(
            [
                ("res_model", "=", records._name),
                ("res_id", "in", records.ids),
                "|",
                ("user_id", "=", self.env.uid),
                ("company_id", "=", self.env.company.id),
            ],
            ["res_id", "user_id", "color_hex"],
        )
        for row in rows:
            found["user" if row["user_id"] else "company"][row["res_id"]] = row["color_hex"]
        return found

    # ------------------------------------------------------------------
    # Setting and clearing overrides
    # ------------------------------------------------------------------

    def bf_color_set_mine(self, color):
        return self._bf_color_set({"user_id": self.env.uid}, color)

    def bf_color_clear_mine(self):
        return self._bf_color_set({"user_id": self.env.uid}, False)

    def bf_color_set_company(self, color):
        self._bf_color_check_company_right()
        return self._bf_color_set({"company_id": self.env.company.id}, color)

    def bf_color_clear_company(self):
        self._bf_color_check_company_right()
        return self._bf_color_set({"company_id": self.env.company.id}, False)

    def _bf_color_check_company_right(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only an administrator can set the company color."))

    def _bf_color_set(self, owner, color):
        """Write (or remove, when ``color`` is falsy) an override for each record."""
        self.check_access("read")
        Override = self.env["bf.color.override"].sudo()
        owner_domain = [(key, "=", value) for key, value in owner.items()]
        existing = Override.search(
            owner_domain + [("res_model", "=", self._name), ("res_id", "in", self.ids)]
        )
        self.invalidate_recordset(BF_COLOR_COMPUTED)
        if not color:
            existing.unlink()
            return True
        value = normalize_hex(color)
        if not value:
            raise ValidationError(_("%s is not a hex color.", color))
        color = value
        existing.write({"color_hex": color})
        missing = set(self.ids) - set(existing.mapped("res_id"))
        Override.create([
            dict(owner, res_model=self._name, res_id=res_id, color_hex=color)
            for res_id in missing
        ])
        return True

    def unlink(self):
        ids, model = self.ids, self._name
        result = super().unlink()
        self.env["bf.color.override"].sudo().search(
            [("res_model", "=", model), ("res_id", "in", ids)]
        ).unlink()
        return result
