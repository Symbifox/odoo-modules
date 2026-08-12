from datetime import datetime

import pytz

from odoo import api, fields, models


def _circular_distance_hours(a, b):
    diff = abs(a - b) % 24
    return min(diff, 24 - diff)


def _closest_preset(presets, now_float):
    candidates = presets.filtered(lambda p: p.default_time)
    if not candidates:
        return candidates.browse()
    return min(candidates, key=lambda p: _circular_distance_hours(p.default_time, now_float))


class MailActivity(models.Model):
    _inherit = "mail.activity"

    time_of_day_id = fields.Many2one(
        "bf.time.of.day",
        string="Plage horaire",
        ondelete="set null",
        index=True,
    )
    time_of_day_color = fields.Integer(
        related="time_of_day_id.color",
        store=True,
        string="Couleur plage",
    )
    time_of_day_icon = fields.Char(
        related="time_of_day_id.icon",
        string="Icône plage",
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if "time_of_day_id" not in fields_list or defaults.get("time_of_day_id"):
            return defaults

        # Quick win D: if the activity is scheduled on a project.task that already
        # carries a slot, inherit it. Falls back to the time-of-now smart default (A).
        res_model = defaults.get("res_model") or self.env.context.get("default_res_model")
        res_id = defaults.get("res_id") or self.env.context.get("default_res_id")
        if res_model == "project.task" and res_id:
            task = self.env["project.task"].browse(res_id).exists()
            if task and task.time_of_day_id:
                defaults["time_of_day_id"] = task.time_of_day_id.id
                return defaults

        # Smart default by current local time.
        tz = pytz.timezone(self.env.user.tz or "UTC")
        now_local = datetime.now(tz)
        now_float = now_local.hour + now_local.minute / 60.0
        presets = self.env["bf.time.of.day"].search([])
        closest = _closest_preset(presets, now_float)
        if closest:
            defaults["time_of_day_id"] = closest.id
        return defaults
