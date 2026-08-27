"""Calendar event button: create a Nextcloud Talk room and store its URL.

Server-side button mirroring core's `set_discuss_videocall_location`. Reads
the per-instance NC config from `ir.config_parameter`, POSTs to the Spreed
OCS API to create a public conversation, then writes the resulting URL into
`videocall_location`. Because the URL does not contain the Discuss route,
`videocall_source` computes to `'custom'` and the value persists.

Two entry points, because the button has to work before the event exists:

- `create_nc_talk_room` is what the calendar form actually calls. It takes a
  room name rather than a recordset, so the quick-create popover can use it
  on an unsaved event; the JS then writes the URL into the in-memory record
  (see `static/src/js/nc_talk_form_controller.js`).
- `action_set_nc_talk_videocall_location` stays as the plain server button, so
  the feature degrades to its old behaviour if the assets bundle fails to load.
"""

import logging

import requests

from odoo import _, api, exceptions, models

_logger = logging.getLogger(__name__)

_SPREED_ROOM_ENDPOINT = "/ocs/v2.php/apps/spreed/api/v4/room"
_TIMEOUT = 15
# Spreed room types: 1=one-to-one, 2=group, 3=public, 4=changelog, 5=formerly-one-to-one
_ROOM_TYPE_PUBLIC = 3


def _http_error_message(status_code, body):
    """Turn a Spreed HTTP failure into something a user can act on.

    The raw OCS payload for an auth failure is `{"ocs":{"meta":{"status":
    "failure","statuscode":401,...}}}`, which tells the person clicking the
    button nothing about what to do. Nextcloud answers 401 both for a wrong
    password and for a revoked app password, and app passwords are the only
    credential that works when the service account has 2FA — so the actionable
    advice is the same in either case: issue a new one and paste it in.
    """
    if status_code == 401:
        return _(
            "Nextcloud refused the service account (HTTP 401). The app "
            "password is wrong, expired or revoked. Create a new app password "
            "for this account in Nextcloud (Settings → Security → Create new "
            "app password) and paste it under Settings → Calendar → "
            "Nextcloud Talk."
        )
    if status_code == 403:
        return _(
            "Nextcloud accepted the service account but refused the action "
            "(HTTP 403). Check that the account is allowed to create "
            "conversations in Talk."
        )
    if status_code == 404:
        return _(
            "Nextcloud has no Talk API at this address (HTTP 404). Check the "
            "URL under Settings → Calendar → Nextcloud Talk, and that the "
            "Talk app is enabled."
        )
    return _(
        "Nextcloud Talk refused the request (HTTP %(code)s): %(body)s"
    ) % {"code": status_code, "body": body[:300]}


def create_talk_room(nc_url, nc_user, password, room_name):
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
            auth=(nc_user, password),
            headers=headers,
            data=payload,
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise exceptions.UserError(_(
            "Could not reach Nextcloud Talk at %(url)s: %(err)s"
        ) % {"url": nc_url, "err": exc}) from exc

    if resp.status_code >= 400:
        raise exceptions.UserError(_http_error_message(resp.status_code, resp.text))

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

    @api.model
    def _get_nc_talk_settings(self):
        """Return (url, user, password), raising if the bridge is unconfigured.

        The Nextcloud account is held in `nc_user`, never `user`: `_()` infers
        the language by reading the caller's frame locals and does `int()` on
        anything it finds under the name `user`. A local called `user` holding
        an account name turns every translated message in this method into
        `ValueError: invalid literal for int()`, which is exactly what the
        unconfigured-instance error used to raise instead of explaining itself.
        """
        params = self.env["ir.config_parameter"].sudo()
        nc_url = params.get_param("bf_calendar_nc_talk.url", "").strip()
        nc_user = params.get_param("bf_calendar_nc_talk.user", "").strip()
        password = params.get_param("bf_calendar_nc_talk.password", "")
        if not (nc_url and nc_user and password):
            raise exceptions.UserError(_(
                "Nextcloud Talk is not configured. Set the URL, service "
                "account and app password under Settings → Calendar → "
                "Nextcloud Talk."
            ))
        return nc_url, nc_user, password

    @api.model
    def create_nc_talk_room(self, room_name=None):
        """Create a Talk room and return its URL, without touching a record.

        Called over RPC by the calendar form controller, including from the
        quick-create popover where no event has been written yet. No leading
        underscore means any client can call it, so it is gated on internal
        users: the room it creates is public and costs a real object on the
        Nextcloud side.
        """
        if not self.env.user._is_internal():
            raise exceptions.AccessError(_(
                "Only internal users can create Nextcloud Talk rooms."
            ))
        nc_url, nc_user, password = self._get_nc_talk_settings()
        token, room_url = create_talk_room(
            nc_url, nc_user, password, room_name=room_name or _("Odoo meeting"),
        )
        _logger.info(
            "bf_calendar_nc_talk: created Talk room %s for %r (user %s)",
            token, room_name, self.env.user.login,
        )
        return room_url

    def action_set_nc_talk_videocall_location(self):
        """Server-side fallback: create the room and write it on a saved event.

        Normally never reached — the form controller intercepts the button and
        calls `create_nc_talk_room` instead. It stays so the button still does
        something if the JS asset does not load.
        """
        self.ensure_one()
        if not self.id:
            raise exceptions.UserError(_(
                "Save the event before creating a Nextcloud Talk room."
            ))
        self.videocall_location = self.create_nc_talk_room(
            room_name=self.name or _("Odoo meeting"),
        )
        return True
