"""Time helpers shared by the route models.

⚠️ A route runs in the time zone of the person RESPONSIBLE for it, not the
server's (UTC) nor the worker's phone: "the 7:30 route" is 7:30 where it runs.
Datetimes are stored naive UTC, as everywhere in Odoo.
"""
from datetime import datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt

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


# Mean Earth radius (m). Good to ~0.3 % anywhere, which is far finer than the
# accuracy a phone reports in a street (5 m at best, often 20 to 60 m).
EARTH_RADIUS_M = 6371008.8


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in metres between two points, as the crow flies, or None.

    ⚠️ Never the road distance: for the gap between a position read at a stop
    and the customer's place, that is what is wanted. A road distance would
    answer "how far did it drive", not "was the person there".

    ⚠️ Whether a point EXISTS is the caller's business, not this function's. An
    Odoo Float reads 0.0 when it was never set, and 0.0 is also a real coordinate:
    the Greenwich meridian for a longitude, the Gulf of Guinea for both. A guard
    written here as ``value in (None, False)`` would read a genuine 0 as missing,
    because ``0.0 == False`` in Python. So the callers test their own fields and
    only ask for a distance once they hold two points they consider set.

    None only when a coordinate was not given at all.
    """
    if None in (lat1, lon1, lat2, lon2):
        return None
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lon2) - radians(lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(a)))
