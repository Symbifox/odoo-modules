"""Calendar event button: create a Nextcloud Talk room and store its URL.

Server-side button mirroring core's `set_discuss_videocall_location`. Reads
the per-instance NC config from `ir.config_parameter`, POSTs to the Spreed
OCS API to create a public conversation, then writes the resulting URL into
`videocall_location`. Because the URL does not contain the Discuss route,
`videocall_source` computes to `'custom'` and the value persists.
"""

import logging

import requests

from odoo import _, exceptions, models

_logger = logging.getLogger(__name__)

_SPREED_ROOM_ENDPOINT = "/ocs/v2.php/apps/spreed/api/v4/room"
_TIMEOUT = 15
# Spreed room types: 1=one-to-one, 2=group, 3=public, 4=changelog, 5=formerly-one-to-one
_ROOM_TYPE_PUBLIC = 3


def create_talk_room(nc_url, user, password, room_name):
    """Create a public Talk conversation. Returns (token, full_url).

    Raises UserError on HTTP / OCS failure with a message safe to show users.
    """
    nc_url = (nc_url or "").rstrip("/")
    endpoint = f"{nc_url}{_SPREED_ROOM_ENDPOINT}"
    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }
    payload = {
        "roomType": _ROOM_TYPE_PUBLIC,
        "roomName": (room_name or "Odoo meeting")[:200],
    }
    try:
        resp = requests.post(
            endpoint,
            auth=(user, password),
            headers=headers,
            data=payload,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise exceptions.UserError(_(
            "Could not reach Nextcloud Talk at %(url)s: %(err)s"
        ) % {"url": nc_url, "err": exc}) from exc

    if resp.status_code >= 400:
        raise exceptions.UserError(_(
            "Nextcloud Talk refused the request (HTTP %(code)s): %(body)s"
        ) % {"code": resp.status_code, "body": resp.text[:300]})

    try:
        data = resp.json()["ocs"]["data"]
    except (ValueError, KeyError) as exc:
        raise exceptions.UserError(_(
            "Unexpected Nextcloud Talk response: %(body)s"
        ) % {"body": resp.text[:300]}) from exc

    token = data.get("token")
    if not token:
        raise exceptions.UserError(_(
            "Nextcloud Talk created no token. Raw data: %(data)s"
        ) % {"data": data})
    return token, f"{nc_url}/call/{token}"


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    def action_set_nc_talk_videocall_location(self):
        self.ensure_one()
        if not self.id:
            raise exceptions.UserError(_(
                "Save the event before creating a Nextcloud Talk room."
            ))
        params = self.env["ir.config_parameter"].sudo()
        nc_url = params.get_param("bf_calendar_nc_talk.url", "").strip()
        user = params.get_param("bf_calendar_nc_talk.user", "").strip()
        password = params.get_param("bf_calendar_nc_talk.password", "")
        if not (nc_url and user and password):
            raise exceptions.UserError(_(
                "Nextcloud Talk is not configured. Set the URL, service "
                "account and app password under Settings → Calendar → "
                "Nextcloud Talk."
            ))
        token, room_url = create_talk_room(
            nc_url, user, password, room_name=self.name or _("Odoo meeting"),
        )
        self.videocall_location = room_url
        _logger.info(
            "bf_calendar_nc_talk: created Talk room %s for event %s (%s)",
            token, self.id, self.name,
        )
        return True
