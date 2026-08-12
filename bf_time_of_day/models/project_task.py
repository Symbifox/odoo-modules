from datetime import datetime

import pytz

from odoo import api, fields, models


def _float_to_hm(value):
    if value is None or value is False:
        return None
    hours = int(value)
    minutes = int(round((value - hours) * 60))
    if minutes == 60:
        hours += 1
        minutes = 0
    return hours % 24, minutes


def _circular_distance_hours(a, b):
    """Smallest distance between two HH.MM floats on a 24h clock."""
    diff = abs(a - b) % 24
    return min(diff, 24 - diff)


def _closest_preset(presets, now_float):
    candidates = presets.filtered(lambda p: p.default_time)
    if not candidates:
        return candidates.browse()
    return min(candidates, key=lambda p: _circular_distance_hours(p.default_time, now_float))


class ProjectTask(models.Model):
    _inherit = "project.task"

    time_of_day_id = fields.Many2one(
        "bf.time.of.day",
        string="Plage horaire",
        ondelete="set null",
        group_expand="_read_group_time_of_day_ids",
        index=True,
        tracking=True,
    )
    time_of_day_color = fields.Integer(
        related="time_of_day_id.color",
        store=True,
        string="Couleur plage",
    )
    time_of_day_icon = fields.Char(
        related="time_of_day_id.icon",
        store=False,
        string="Icône plage",
    )
    time_of_day_code = fields.Selection(
        selection=[
            ("morning", "Matinée"),
            ("midday", "Midi"),
            ("eod", "Fin de jour"),
            ("after_hours", "Hors heures"),
        ],
        string="Code plage horaire",
        compute="_compute_time_of_day_code",
        store=True,
        index=True,
        help="Code stable de la plage horaire, miroir de time_of_day_id.code. "
        "Sert de clé à la barre de progression kanban (colors mappés par code). "
        "Limité aux 4 plages livrées par le module : une plage admin ajoutée "
        "hors de cette liste laisse le champ vide.",
    )

    @api.depends("time_of_day_id", "time_of_day_id.code")
    def _compute_time_of_day_code(self):
        valid = dict(self._fields["time_of_day_code"].selection)
        for task in self:
            code = task.time_of_day_id.code
            task.time_of_day_code = code if code in valid else False

    @api.model
    def _read_group_time_of_day_ids(self, presets, domain):
        return self.env["bf.time.of.day"].search([])

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if "time_of_day_id" in fields_list and not defaults.get("time_of_day_id"):
            tz = pytz.timezone(self.env.user.tz or "UTC")
            now_local = datetime.now(tz)
            now_float = now_local.hour + now_local.minute / 60.0
            presets = self.env["bf.time.of.day"].search([])
            closest = _closest_preset(presets, now_float)
            if closest:
                defaults["time_of_day_id"] = closest.id
        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._apply_time_of_day_to_deadline()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "time_of_day_id" in vals or ("date_deadline" in vals and self.filtered("time_of_day_id")):
            for rec in self:
                rec._apply_time_of_day_to_deadline()
        return res

    def _apply_time_of_day_to_deadline(self):
        """If a slot is set and the deadline is set, rewrite the deadline's time
        portion to the user's effective time for that slot. Date is preserved."""
        self.ensure_one()
        if not self.time_of_day_id or not self.date_deadline:
            return
        effective = self.env.user._tod_effective_time(self.time_of_day_id)
        if effective is False or effective is None:
            return
        hm = _float_to_hm(effective)
        if hm is None:
            return
        hours, minutes = hm
        tz = pytz.timezone(self.env.user.tz or "UTC")
        local = pytz.utc.localize(self.date_deadline.replace(tzinfo=None)).astimezone(tz)
        new_local = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        new_utc = new_local.astimezone(pytz.utc).replace(tzinfo=None)
        if new_utc != self.date_deadline:
            super(ProjectTask, self).write({"date_deadline": new_utc})
