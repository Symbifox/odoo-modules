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
    _description = "Plage horaire"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Identifiant stable (morning, midday, …).")
    sequence = fields.Integer(default=10)
    color = fields.Integer(default=0)
    icon = fields.Char(
        help="Classe Font Awesome (ex. fa-coffee, fa-sun-o, fa-moon-o). "
        "Préfixée en émoji dans le menu déroulant.",
    )
    default_time = fields.Float(
        string="Heure suggérée",
        help="Heure-cadran (HH:MM) appliquée à la deadline d'une tâche quand "
        "cette plage est sélectionnée. Laisser vide pour ne rien forcer.",
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("code_uniq", "unique(code)", "Le code de la plage horaire doit être unique."),
    ]

    @api.depends("name", "icon")
    def _compute_display_name(self):
        for rec in self:
            emoji = ICON_TO_EMOJI.get(rec.icon or "", "")
            rec.display_name = f"{emoji} {rec.name}".strip() if emoji else (rec.name or "")
