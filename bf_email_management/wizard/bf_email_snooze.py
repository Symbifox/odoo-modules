"""Snooze wizard — defer bf.email rows out of the inbox until a date.

Sets ``is_handled=True`` and ``snoozed_until=<datetime>`` on selected rows.
The IMAP mirror cron flips them back to ``is_handled=False`` once the
``snoozed_until`` timestamp passes.
"""

from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models


class BfEmailSnooze(models.TransientModel):
    _name = "bf.email.snooze"
    _description = "Reporter des courriels"

    bf_email_ids = fields.Many2many(
        comodel_name="bf.email",
        string="Courriels",
        required=True,
    )
    bf_email_count = fields.Integer(
        string="Nombre",
        compute="_compute_count",
    )
    preset = fields.Selection(
        selection=[
            ("1h", "Dans 1 heure"),
            ("3h", "Dans 3 heures"),
            ("tonight", "Ce soir (18h)"),
            ("tomorrow", "Demain matin (8h)"),
            ("nextweek", "Lundi prochain (8h)"),
            ("custom", "Date personnalisée"),
        ],
        string="Préréglage",
        default="tomorrow",
        required=True,
    )
    snoozed_until = fields.Datetime(
        string="Jusqu'à",
        help="Au-delà de cette date, le courriel revient automatiquement "
             "dans la boîte de réception.",
    )

    @api.depends("bf_email_ids")
    def _compute_count(self):
        for rec in self:
            rec.bf_email_count = len(rec.bf_email_ids)

    @api.onchange("preset")
    def _onchange_preset(self):
        """Resolve the preset in the USER's timezone, not UTC.

        ``fields.Datetime.now()`` is naive UTC, so ``.replace(hour=18)`` used to
        mean 18:00 UTC — 14:00 in Montreal. "Ce soir" fired mid-afternoon and
        "Demain matin (8 h)" at 4 a.m., which is exactly when a snoozed email
        should NOT come back. Wall-clock hours only mean something in a
        timezone, so the arithmetic happens there and converts back to UTC for
        storage.
        """
        now = fields.Datetime.now()
        tz = pytz.timezone(self.env.user.tz or "America/Montreal")
        local_now = now.replace(tzinfo=pytz.UTC).astimezone(tz)

        def at(day_offset, hour):
            target = (local_now + timedelta(days=day_offset)).date()
            naive = datetime.combine(target, time(hour=hour))
            return tz.localize(naive).astimezone(pytz.UTC).replace(tzinfo=None)

        if self.preset == "1h":
            self.snoozed_until = now + timedelta(hours=1)
        elif self.preset == "3h":
            self.snoozed_until = now + timedelta(hours=3)
        elif self.preset == "tonight":
            tonight = at(0, 18)
            self.snoozed_until = tonight if tonight > now else at(1, 18)
        elif self.preset == "tomorrow":
            self.snoozed_until = at(1, 8)
        elif self.preset == "nextweek":
            days_ahead = (7 - local_now.weekday()) % 7 or 7
            self.snoozed_until = at(days_ahead, 8)

    def action_apply(self):
        self.ensure_one()
        if not self.snoozed_until:
            return
        self.bf_email_ids.write({
            "is_handled": True,
            "handled_at": fields.Datetime.now(),
            "snoozed_until": self.snoozed_until,
        })
        return {"type": "ir.actions.act_window_close"}
