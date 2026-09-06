from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.studio_light_field import (
    SUPPORTED_TYPES,
    is_model_locked,
    slugify_field_name,
)


class StudioLightWizard(models.TransientModel):
    _name = "studio.light.wizard"
    _description = "Forge — New custom field"

    # Step 1 — pick the target
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        domain="[('transient', '=', False)]",
        help="The record type to extend (e.g. Contact = res.partner).",
    )

    # Step 2 — describe the field
    label = fields.Char(string="Field label", required=True)
    field_name = fields.Char(
        string="Technical name",
        help="Auto-derived from label. Must start with x_studio_.",
    )
    field_type = fields.Selection(SUPPORTED_TYPES, default="char", required=True)
    help_text = fields.Char(string="Help text shown to users")
    required = fields.Boolean()

    # Type-specific
    selection_text = fields.Text(
        string="Selection values",
        help="One per line, format: key=Label (e.g. gold=Gold member).",
    )
    relation_model_id = fields.Many2one(
        "ir.model",
        string="Linked model",
        help="For Many2one / Many2many: which model the field points to.",
    )
    reference_model_ids = fields.Many2many(
        "ir.model",
        string="Allowed reference models",
        help="For Reference: the whitelist of target models the user can "
        "pick from when populating this field.",
    )

    is_related = fields.Boolean(
        string="Computed from another field",
        help="Read the value from a related path (e.g. partner_id.email).",
    )
    related_path = fields.Char(
        string="Related path",
        help="Dotted path from this model. Examples: partner_id.email, "
        "partner_id.country_id.name.",
    )

    # Step 3 — view injection (optional)
    inject_in_form = fields.Boolean(string="Show in form view", default=True)
    target_field = fields.Char(
        string="Place after field",
        help="Existing field name on the form view to anchor on (e.g. 'email').",
    )
    inject_in_list = fields.Boolean(string="Show in list view")
    inject_in_kanban = fields.Boolean(string="Show in kanban card")
    inject_in_search = fields.Boolean(string="Show in search filters")

    # ------------------------------------------------------------------
    # Onchange helpers
    # ------------------------------------------------------------------
    @api.onchange("label")
    def _onchange_label(self):
        if self.label and not self.field_name:
            self.field_name = slugify_field_name(self.label)

    @api.onchange("field_name")
    def _onchange_field_name(self):
        if self.field_name and not self.field_name.startswith("x_studio_"):
            self.field_name = slugify_field_name(self.field_name)

    # ------------------------------------------------------------------
    # Action
    # ------------------------------------------------------------------
    def _parse_selection_text(self):
        self.ensure_one()
        out = []
        if not self.selection_text:
            return out
        for line in self.selection_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                raise UserError(
                    _("Selection line %r must use the format key=Label.") % line
                )
            key, label = line.split("=", 1)
            out.append({"key": key.strip(), "label": label.strip()})
        if not out:
            raise UserError(_("Add at least one selection value."))
        return out

    def action_create(self):
        self.ensure_one()
        if not self.model_id:
            raise UserError(_("Choose a model first."))
        if is_model_locked(self.model_id.model) and not self.env.user.has_group(
            "bf_studio_light.group_studio_light_unlocked"
        ):
            raise UserError(
                _(
                    "Model %s is locked. Ask a sysadmin to grant you the "
                    "'Forge: Bypass model lock' group if you really "
                    "need to extend this model."
                )
                % self.model_id.model
            )

        field_name = self.field_name or slugify_field_name(self.label)
        if not field_name.startswith("x_studio_"):
            field_name = slugify_field_name(field_name)

        field_vals = {
            "name": field_name,
            "label": self.label,
            "help_text": self.help_text,
            "model_id": self.model_id.id,
            "field_type": self.field_type,
            "required": self.required and not self.is_related,
            "is_related": self.is_related,
            "related_path": self.related_path,
        }
        if self.field_type == "selection" and not self.is_related:
            field_vals["selection_ids"] = [
                (
                    0,
                    0,
                    {
                        "key": s["key"],
                        "label": s["label"],
                        "sequence": (i + 1) * 10,
                    },
                )
                for i, s in enumerate(self._parse_selection_text())
            ]
        if self.field_type in ("many2one", "many2many") and not self.is_related:
            if not self.relation_model_id:
                raise UserError(
                    _("Pick the linked model for the %s field.") % self.field_type
                )
            field_vals["relation_model_id"] = self.relation_model_id.id
        if self.field_type == "reference" and not self.is_related:
            if not self.reference_model_ids:
                raise UserError(
                    _(
                        "Pick at least one allowed target model for the "
                        "reference field."
                    )
                )
            field_vals["reference_model_ids"] = [(6, 0, self.reference_model_ids.ids)]

        studio_field = self.env["studio.light.field"].create(field_vals)

        Inj = self.env["studio.light.view.injection"]
        if self.inject_in_form and self.target_field:
            Inj.create(
                {
                    "name": f"Field {studio_field.name} on {studio_field.model_name} (form)",
                    "studio_field_id": studio_field.id,
                    "model_id": self.model_id.id,
                    "view_type": "form",
                    "target_field": self.target_field,
                    "position": "after",
                }
            )
        for vtype, xpath in (
            ("list", "//list"),
            ("kanban", "//templates"),
            ("search", "//search"),
        ):
            if not getattr(self, f"inject_in_{vtype}"):
                continue
            Inj.create(
                {
                    "name": f"Field {studio_field.name} on {studio_field.model_name} ({vtype})",
                    "studio_field_id": studio_field.id,
                    "model_id": self.model_id.id,
                    "view_type": vtype,
                    "custom_xpath": xpath,
                    "position": "inside",
                }
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Custom field created"),
                "message": _(
                    "Field %s is live on %s. Reload your browser tab "
                    "to see it on existing forms — Odoo caches view "
                    "metadata per session."
                ) % (studio_field.name, studio_field.model_name),
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "studio.light.field",
                    "res_id": studio_field.id,
                    "view_mode": "form",
                    "target": "current",
                },
            },
        }
