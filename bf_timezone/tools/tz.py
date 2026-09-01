# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Pure, stateless timezone helpers shared across Blue Fox modules.

Single source of truth for the timezone bits that used to be copy-pasted in
``bf_meeting``, ``bf_appointment`` and ``calendar_nextcloud_sync``:
the UTC->local conversion, the Montréal/Auckland city labels, the
Windows->IANA name normalization and the "which tz should I show" resolver.

These functions take no Odoo ``env`` so they can be called from anywhere
(controllers, ICS generation, cron). Code that needs the configurable default
timezone should go through the ``bf.timezone`` model wrapper, which reads the
``bf_timezone.default_tz`` system parameter.
"""

import pytz

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback
    from backports.zoneinfo import ZoneInfo

# The ultimate fallback when no timezone can be resolved and no configurable
# default is available. The configurable default lives in the
# ``bf_timezone.default_tz`` system parameter (see models/bf_timezone.py).
DEFAULT_TZ = "America/Toronto"

# Windows timezone names -> IANA, for the common values seen in ICS/CalDAV
# feeds produced by Outlook/Exchange.
WINDOWS_TZ_MAP = {
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "US Eastern Standard Time": "America/Indianapolis",
    "Eastern Daylight Time": "America/New_York",
    "Central Daylight Time": "America/Chicago",
    "Mountain Daylight Time": "America/Denver",
    "Pacific Daylight Time": "America/Los_Angeles",
    "Romance Standard Time": "Europe/Paris",
    "W. Europe Standard Time": "Europe/Berlin",
    "GMT Standard Time": "Europe/London",
    "UTC": "UTC",
    # Asie-Pacifique. Ajoutés le 2026-09-01 après qu'une invitation Calendly
    # néo-zélandaise soit arrivée décalée de DOUZE HEURES : le nom n'était pas
    # dans cette table, la résolution échouait, et l'analyseur du .ics prenait
    # alors l'heure murale locale pour de l'UTC.
    "New Zealand Standard Time": "Pacific/Auckland",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "AUS Central Standard Time": "Australia/Darwin",
    "E. Australia Standard Time": "Australia/Brisbane",
    "W. Australia Standard Time": "Australia/Perth",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "Tasmania Standard Time": "Australia/Hobart",
    "Fiji Standard Time": "Pacific/Fiji",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "India Standard Time": "Asia/Kolkata",
    # Europe et Amériques restantes, tant qu'à ouvrir le fichier.
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kyiv",
    "GTB Standard Time": "Europe/Bucharest",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "South Africa Standard Time": "Africa/Johannesburg",
    "Israel Standard Time": "Asia/Jerusalem",
    "Arabian Standard Time": "Asia/Dubai",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Eastern Standard Time": "America/Cayenne",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
}

# IANA timezone -> human city label shown to users. Extend as Blue Fox's
# footprint grows. Anything not listed falls back to the last path segment.
TZ_CITY_LABEL = {
    "America/Toronto": "Montréal",
    "America/Montreal": "Montréal",
    "Pacific/Auckland": "Auckland",
}


def normalize_name(tzid):
    """Map a Windows tz name to IANA and strip a leading slash.

    Returns the input unchanged when it is falsy or already IANA.

    ⚠️ Cette table est une COMMODITÉ, pas la source de vérité. Elle ne peut pas
    être exhaustive et se périme; un `.ics` dont le TZID n'y figure pas doit
    être résolu par son propre bloc `VTIMEZONE`, qui est autoritaire et
    n'exige aucun entretien. Voir
    `calendar_nextcloud_sync._ics_vtimezones`. Un nom manquant ici n'est donc
    plus une panne, seulement un chemin plus lent.
    """
    if not tzid:
        return tzid
    mapped = WINDOWS_TZ_MAP.get(tzid)
    if mapped:
        return mapped
    if tzid.startswith("/"):
        tzid = tzid[1:]
    return tzid


def tz_city(name):
    """Human city label for a timezone (``America/Toronto`` -> ``Montréal``)."""
    if not name:
        return ""
    if name in TZ_CITY_LABEL:
        return TZ_CITY_LABEL[name]
    return name.split("/")[-1].replace("_", " ")


def to_tz(dt, name):
    """Convert a datetime to timezone ``name``.

    Naive datetimes are assumed to be UTC (Odoo's storage convention).
    """
    if dt.tzinfo is None:
        aware = pytz.utc.localize(dt)
    else:
        aware = dt.astimezone(pytz.utc)
    return aware.astimezone(pytz.timezone(name))


def resolve(candidates, fallback=DEFAULT_TZ, validate=False):
    """Return the first usable timezone name from ``candidates``.

    Falsy candidates are skipped. When ``validate`` is True, candidates that
    are not loadable as a real timezone are skipped too. ``fallback`` is
    returned when nothing matches.
    """
    for candidate in candidates:
        if not candidate:
            continue
        if validate:
            try:
                ZoneInfo(candidate)
            except Exception:
                continue
        return candidate
    return fallback


def format_dual(dt, primary_tz, secondary_tz, fmt="%Y-%m-%d %H:%M %Z"):
    """Format ``dt`` in ``primary_tz``, appending the ``secondary_tz`` reading
    in parentheses with its city label when the two timezones differ.
    """
    primary = to_tz(dt, primary_tz).strftime(fmt)
    if primary_tz == secondary_tz:
        return primary
    secondary = to_tz(dt, secondary_tz).strftime(fmt)
    return f"{primary} ({tz_city(secondary_tz)}: {secondary})"
