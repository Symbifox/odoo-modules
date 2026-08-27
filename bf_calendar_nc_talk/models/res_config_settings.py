"""Per-instance Nextcloud Talk endpoint + service account.

Three `ir.config_parameter` rows hold the bridge config:
- `bf_calendar_nc_talk.url`       — NC base URL (no trailing slash)
- `bf_calendar_nc_talk.user`      — service account NC username
- `bf_calendar_nc_talk.password`  — service account app password

Each Odoo tenant points at its own Nextcloud, so the same module installed
on different DBs creates rooms on different NC instances.
"""

from odoo import _, exceptions, fields, models

from .calendar_event import create_talk_room


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_nc_talk_url = fields.Char(
        string="Nextcloud URL",
        config_parameter="bf_calendar_nc_talk.url",
        help="Base URL of the Nextcloud instance hosting Talk "
             "(e.g. https://nextcloud.bluefoxconsultant.com). No trailing slash.",
    )
    bf_nc_talk_user = fields.Char(
        string="Service account",
        config_parameter="bf_calendar_nc_talk.user",
        help="Nextcloud username used to create Talk rooms on behalf of users.",
    )
    bf_nc_talk_password = fields.Char(
        string="App password",
        config_parameter="bf_calendar_nc_talk.password",
        help="Nextcloud app password for the service account "
             "(Settings → Security → Create new app password).",
    )

    def action_test_nc_talk_connection(self):
        """Create a throwaway room so the settings can be checked before use.

        `nc_user`, not `user`: see CalendarEvent._get_nc_talk_settings — `_()`
        calls `int()` on a frame local named `user`, so naming it that turns
        the "fill in the fields first" error into a ValueError.
        """
        self.ensure_one()
        url = (self.bf_nc_talk_url or "").rstrip("/")
        nc_user = self.bf_nc_talk_user or ""
        pwd = self.bf_nc_talk_password or ""
        if not (url and nc_user and pwd):
            raise exceptions.UserError(_(
                "Fill in URL, service account and app password before testing."
            ))
        token, room_url = create_talk_room(
            url, nc_user, pwd, room_name="Odoo — test connection",
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Nextcloud Talk OK"),
                "message": _("Created test room %(token)s at %(url)s") % {
                    "token": token, "url": room_url,
                },
                "type": "success",
                "sticky": False,
            },
        }
