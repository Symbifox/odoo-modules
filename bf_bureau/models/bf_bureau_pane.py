import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .bf_bureau_desk import LAYOUT_SLOTS


VIEW_TYPES = [
    ("kanban", "Kanban"),
    ("list", "List"),
    ("form", "Form"),
    ("pivot", "Pivot table"),
    ("graph", "Graph"),
    ("calendar", "Calendar"),
    ("activity", "Activity"),
]


class BfBureauPane(models.Model):
    _name = "bf.bureau.pane"
    _description = "Desk pane"
    _order = "desk_id, slot"

    desk_id = fields.Many2one(
        "bf.bureau.desk",
        required=True,
        ondelete="cascade",
        index=True,
    )
    slot = fields.Selection(
        [
            ("full", "Full screen"),
            ("left_full", "Left column"),
            ("right_full", "Right column"),
            ("top_full", "Top row"),
            ("top_left", "Top left"),
            ("top_right", "Top right"),
            ("bottom_full", "Bottom row"),
            ("bottom_left", "Bottom left"),
            ("bottom_right", "Bottom right"),
            ("row_1", "Row 1"),
            ("row_2", "Row 2"),
            ("row_3", "Row 3"),
        ],
        required=True,
    )
    action_id = fields.Many2one(
        "ir.actions.act_window",
        required=True,
        ondelete="restrict",
        domain="[('type','=','ir.actions.act_window')]",
    )
    view_type = fields.Selection(
        VIEW_TYPES,
        default="kanban",
        required=True,
    )
    name_override = fields.Char()
    weight = fields.Integer(
        default=1,
        help="Relative weight (1 = normal, 2 = large, 3 = very large). "
             "Affects the size of the pane in the grid.",
    )
    domain_override = fields.Char(
        help="Python expression added (AND) to the action's domain. E.g.: "
             "[('priority','=','1')]. Leave empty to use the action's "
             "domain as is.",
    )
    context_override = fields.Char(
        help="Python dictionary merged over the action's context. E.g.: "
             "{'search_default_my_filter': 1}. Leave empty to use the "
             "action's context as is.",
    )

    _sql_constraints = [
        (
            "desk_slot_unique",
            "UNIQUE (desk_id, slot)",
            "Only one pane per slot in a desk.",
        ),
        (
            "weight_positive",
            "CHECK (weight >= 1 AND weight <= 4)",
            "The weight must be between 1 and 4.",
        ),
    ]

    @api.constrains("slot", "desk_id")
    def _check_slot_layout(self):
        for pane in self:
            allowed = LAYOUT_SLOTS.get(pane.desk_id.layout, ())
            if pane.slot not in allowed:
                raise ValidationError(_(
                    "The slot \"%(slot)s\" is not valid for the layout \"%(layout)s\". "
                    "Allowed slots: %(allowed)s.",
                    slot=pane.slot, layout=pane.desk_id.layout, allowed=", ".join(allowed),
                ))

    @api.constrains("view_type", "action_id")
    def _check_view_type_in_action(self):
        for pane in self:
            modes = (pane.action_id.view_mode or "").split(",")
            if pane.view_type not in [m.strip() for m in modes]:
                raise ValidationError(_(
                    "The view type \"%(view)s\" is not supported by the action "
                    "\"%(action)s\" (available modes: %(modes)s).",
                    view=pane.view_type, action=pane.action_id.name,
                    modes=pane.action_id.view_mode,
                ))

    @api.constrains("domain_override")
    def _check_domain_override_parses(self):
        for pane in self:
            if not pane.domain_override:
                continue
            try:
                value = ast.literal_eval(pane.domain_override)
            except (SyntaxError, ValueError) as exc:
                raise ValidationError(_(
                    "Invalid domain: %s. It must be a Python list, "
                    "e.g. [('priority','=','1')].", exc,
                ))
            if not isinstance(value, list):
                raise ValidationError(_("Invalid domain: it must be a Python list."))

    @api.constrains("context_override")
    def _check_context_override_parses(self):
        for pane in self:
            if not pane.context_override:
                continue
            try:
                value = ast.literal_eval(pane.context_override)
            except (SyntaxError, ValueError) as exc:
                raise ValidationError(_(
                    "Invalid context: %s. It must be a Python dictionary, "
                    "e.g. {'search_default_my_filter': 1}.", exc,
                ))
            if not isinstance(value, dict):
                raise ValidationError(_("Invalid context: it must be a Python dictionary."))
