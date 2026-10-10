"""Time helpers shared by the route models.

⚠️ A route runs in the time zone of the person RESPONSIBLE for it, not the
server's (UTC) nor the worker's phone: "the 7:30 route" is 7:30 where it runs.
Datetimes are stored naive UTC, as everywhere in Odoo.
"""
from datetime import datetime, time, timedelta

import pytz

from odoo import fields


def tz_of(*candidates):
    """The first time zone set among ``candidates`` (users or partners), else UTC.

    ⚠️ Never the user who happens to run the code: the watcher runs as a system user
    without a time zone, and the same day would start at another hour from the office.
    """
    for candidate in candidates:
        name = candidate and candidate.tz
        if name:
            try:
                return pytz.timezone(name)
            except pytz.UnknownTimeZoneError:
                continue
    return pytz.utc


def float_to_time(hours):
    """7.5 -> 07:30. Out-of-range values are clamped, never rejected."""
    hours = max(0.0, min(hours or 0.0, 23.99))
    whole = int(hours)
    minutes = min(int(round((hours - whole) * 60)), 59)
    return time(whole, minutes)


def local_to_utc(tz, day, hours):
    """A local date and a float hour -> naive UTC datetime."""
    local = tz.localize(datetime.combine(day, float_to_time(hours)))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


def utc_to_local(tz, value):
    return pytz.utc.localize(value).astimezone(tz)


def local_today(tz, now=None):
    return utc_to_local(tz, now or fields.Datetime.now()).date()


def parse_client_time(value, now=None, earliest=None):
    """The phone's time of a mark, as naive UTC, or None when it cannot be trusted.

    The phone's clock is kept because a mark made in a basement is sent later:
    its server time would be wrong. It is refused when it lies in the future
    (more than five minutes ahead) or before ``earliest``.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(pytz.utc).replace(tzinfo=None)
    now = now or fields.Datetime.now()
    if parsed > now + timedelta(minutes=5):
        return None
    if earliest and parsed < earliest:
        return None
    return parsed
