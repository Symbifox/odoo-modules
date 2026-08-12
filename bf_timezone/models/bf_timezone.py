# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import api, models

from ..tools import tz as tz_tools

DEFAULT_TZ_PARAM = "bf_timezone.default_tz"


class BfTimezone(models.AbstractModel):
    """Env-aware façade over ``tools.tz``.

    Adds the one thing the pure helpers cannot know on their own: the
    configurable default timezone, read from the ``bf_timezone.default_tz``
    system parameter. Callers do ``self.env['bf.timezone'].<helper>(...)``.
    """

    _name = "bf.timezone"
    _description = "Blue Fox Timezone Helpers"

    @api.model
    def default_tz(self):
        """The configured default/fallback timezone (one place to flip MTL->AKL)."""
        param = self.env["ir.config_parameter"].sudo().get_param(DEFAULT_TZ_PARAM)
        return param or tz_tools.DEFAULT_TZ

    @api.model
    def normalize_name(self, tzid):
        return tz_tools.normalize_name(tzid)

    @api.model
    def tz_city(self, name):
        return tz_tools.tz_city(name)

    @api.model
    def to_tz(self, dt, name):
        return tz_tools.to_tz(dt, name)

    @api.model
    def resolve(self, candidates, fallback=None, validate=False):
        """First usable tz among ``candidates``, else the configured default."""
        if fallback is None:
            fallback = self.default_tz()
        return tz_tools.resolve(candidates, fallback=fallback, validate=validate)

    @api.model
    def format_dual(self, dt, primary_tz, secondary_tz, fmt="%Y-%m-%d %H:%M %Z"):
        return tz_tools.format_dual(dt, primary_tz, secondary_tz, fmt=fmt)
