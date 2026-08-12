# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import pytz

from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_default_tz = fields.Selection(
        "_bf_tz_get",
        string="Default timezone",
        config_parameter="bf_timezone.default_tz",
        default="America/Toronto",
        help="Fallback timezone used by Blue Fox modules when no contact, "
             "user or calendar timezone is available (e.g. meeting displays, "
             "appointment emails, ICS files). Set this to Pacific/Auckland "
             "after relocating.",
    )

    @api.model
    def _bf_tz_get(self):
        return [
            (tz, tz)
            for tz in sorted(
                pytz.all_timezones,
                key=lambda tz: tz if not tz.startswith("Etc/") else "_" + tz,
            )
        ]
