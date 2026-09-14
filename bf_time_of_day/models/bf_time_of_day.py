from odoo import api, fields, models


ICON_TO_EMOJI = {
    "fa-coffee": "☕",
    "fa-sun-o": "🌞",
    "fa-clock-o": "🕓",
    "fa-moon-o": "🌙",
    "fa-cutlery": "🍽️",
    "fa-bed": "🛏️",
    "fa-bolt": "⚡",
    "fa-leaf": "🌿",
    "fa-fire": "🔥",
    "fa-star": "⭐",
}


class BfTimeOfDay(models.Model):
    _name = "bf.time.of.day"
    _description = "Time slot"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Stable identifier (morning, "
                                           "midday, …).")
    sequence = fields.Integer(default=10)
    color = fields.Integer(default=0)
    icon = fields.Char(
        help="Font Awesome class (e.g. fa-coffee, fa-sun-o, fa-moon-o). "
             "Shown as a prefix in the drop-down menu.",
    )
    default_time = fields.Float(
        string="Suggested time",
        help="Clock time (HH:MM) applied to a task's deadline when this "
             "slot is selected. Leave empty to force nothing.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "The time slot code must be unique."),
    ]

    @api.depends("name", "icon")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for rec in self:
            emoji = ICON_TO_EMOJI.get(rec.icon or "", "")
            rec.display_name = f"{emoji} {rec.name}".strip() if emoji else (rec.name or "")
