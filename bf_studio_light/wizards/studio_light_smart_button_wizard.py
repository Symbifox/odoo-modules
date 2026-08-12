from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..models.studio_light_smart_button import COLOR_CHOICES


class StudioLightSmartButtonWizard(models.TransientModel):
    _name = "studio.light.smart.button.wizard"
    _description = "Studio Light — Add smart button wizard"

    source_model_id = fields.Many2one(
        "ir.model",
        required=True,
        string="Source model",
        domain="[('transient', '=', False)]",
    )
    target_model_id = fields.Many2one(
        "ir.model",
        required=True,
        string="Target model",
        domain="[('transient', '=', False)]",
    )
    relation_field_id = fields.Many2one(
        "ir.model.fields",
        required=True,
        string="Relation field on target",
        domain="[('model_id', '=', target_model_id), ('ttype', '=', 'many2one')]",
    )
    label = fields.Char(required=True)
    icon = fields.Char(default="fa-list", required=True)
    color = fields.Selection(
        COLOR_CHOICES, default="text-primary", required=True
    )
    domain = fields.Char(
        string="Extra domain (optional)",
        help="Optional Python literal list of 3-tuples narrowing the count.",
    )
    open_on_click = fields.Boolean(default=True)

    @api.onchange("target_model_id")
    def _onchange_target_model_id(self):
        # Clear relation_field if it stops matching the new target.
        if (
            self.relation_field_id
            and self.relation_field_id.model_id != self.target_model_id
        ):
            self.relation_field_id = False

    def action_create(self):
        self.ensure_one()
        if not self.label:
            raise ValidationError(_("Label is required."))
        sb = self.env["studio.light.smart.button"].create(
            {
                "name": self.label,
                "source_model_id": self.source_model_id.id,
                "target_model_id": self.target_model_id.id,
                "relation_field_id": self.relation_field_id.id,
                "label": self.label,
                "icon": self.icon,
                "color": self.color,
                "domain": self.domain or False,
                "open_on_click": self.open_on_click,
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Smart button created"),
                "message": _(
                    "Button %s is live on %s. Reload your browser tab "
                    "to see it on existing forms — Odoo caches view "
                    "metadata per session."
                ) % (sb.label, sb.source_model_name),
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "studio.light.smart.button",
                    "res_id": sb.id,
                    "view_mode": "form",
                    "target": "current",
                },
            },
        }
