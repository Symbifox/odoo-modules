from odoo import models

from odoo.addons.bf_color.models.color_utils import normalize_hex


class CalendarEvent(models.Model):
    _name = "calendar.event"
    _inherit = ["calendar.event", "bf.color.mixin"]


class NextcloudCalendarSyncConfig(models.Model):
    _name = "nextcloud.calendar.sync.config"
    _inherit = ["nextcloud.calendar.sync.config", "bf.color.mixin"]

    def _bf_color_own(self):
        """A Nextcloud calendar's own color is the one Nextcloud gives it.

        Odoo kept only the closest of twelve (odoo_color, set by hand): the
        personal calendar, mauve in Nextcloud, showed red in Odoo.
        """
        own = normalize_hex(self.color_hex) or normalize_hex(self.calendar_color)
        return own or super()._bf_color_own()
