# Copyright 2026 Les Services de consultation Blue Fox Inc.
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from datetime import datetime

import pytz

from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_timezone.tools import tz


@tagged("bf_timezone", "bf_timezone_tools")
class TestTzTools(TransactionCase):
    """Pure timezone helpers in bf_timezone/tools/tz.py — the single source of
    truth that used to be copy-pasted across bf_meeting/bf_appointment/
    calendar_nextcloud_sync."""

    # ------------------------------------------------------------------ #
    # normalize_name
    # ------------------------------------------------------------------ #
    def test_normalize_name_all_windows_entries(self):
        """Every Windows alias maps to its IANA name."""
        for windows_name, iana in tz.WINDOWS_TZ_MAP.items():
            self.assertEqual(
                tz.normalize_name(windows_name), iana,
                "Windows tz %r should map to %r" % (windows_name, iana),
            )

    def test_normalize_name_strip_leading_slash(self):
        """Some ICS feeds prefix a slash (e.g. /America/Montreal)."""
        self.assertEqual(tz.normalize_name("/America/Montreal"), "America/Montreal")

    def test_normalize_name_already_iana_unchanged(self):
        self.assertEqual(tz.normalize_name("America/Toronto"), "America/Toronto")
        self.assertEqual(tz.normalize_name("Pacific/Auckland"), "Pacific/Auckland")

    def test_normalize_name_falsy_passthrough(self):
        """Falsy input is returned unchanged (callers rely on this)."""
        self.assertEqual(tz.normalize_name(""), "")
        self.assertIsNone(tz.normalize_name(None))

    def test_normalize_name_unknown_unchanged(self):
        self.assertEqual(tz.normalize_name("Foo/Bar"), "Foo/Bar")

    # ------------------------------------------------------------------ #
    # tz_city
    # ------------------------------------------------------------------ #
    def test_tz_city_mapped(self):
        self.assertEqual(tz.tz_city("America/Toronto"), "Montréal")
        self.assertEqual(tz.tz_city("America/Montreal"), "Montréal")
        self.assertEqual(tz.tz_city("Pacific/Auckland"), "Auckland")

    def test_tz_city_unmapped_last_segment(self):
        self.assertEqual(tz.tz_city("Europe/Paris"), "Paris")
        self.assertEqual(
            tz.tz_city("America/Argentina/Buenos_Aires"), "Buenos Aires"
        )

    def test_tz_city_empty(self):
        self.assertEqual(tz.tz_city(""), "")
        self.assertEqual(tz.tz_city(None), "")

    # ------------------------------------------------------------------ #
    # to_tz
    # ------------------------------------------------------------------ #
    def test_to_tz_naive_assumed_utc_winter(self):
        """Naive datetimes are treated as UTC; winter = EST (-5)."""
        dt = datetime(2026, 1, 15, 12, 0, 0)
        local = tz.to_tz(dt, "America/Toronto")
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M %Z"), "2026-01-15 07:00 EST")

    def test_to_tz_naive_assumed_utc_summer_dst(self):
        """Summer = EDT (-4); proves DST is honoured."""
        dt = datetime(2026, 7, 15, 12, 0, 0)
        local = tz.to_tz(dt, "America/Toronto")
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M %Z"), "2026-07-15 08:00 EDT")

    def test_to_tz_auckland_dst(self):
        """Auckland: Jan = NZDT (+13), Jul = NZST (+12)."""
        summer = tz.to_tz(datetime(2026, 1, 15, 0, 0, 0), "Pacific/Auckland")
        self.assertEqual(summer.strftime("%H:%M %Z"), "13:00 NZDT")
        winter = tz.to_tz(datetime(2026, 7, 15, 0, 0, 0), "Pacific/Auckland")
        self.assertEqual(winter.strftime("%H:%M %Z"), "12:00 NZST")

    def test_to_tz_aware_input(self):
        """An already-aware datetime is converted, not double-localized."""
        aware = pytz.timezone("America/Toronto").localize(datetime(2026, 1, 15, 7, 0, 0))
        local = tz.to_tz(aware, "Pacific/Auckland")
        # 2026-01-15 07:00 EST == 12:00 UTC == 2026-01-16 01:00 NZDT
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M %Z"), "2026-01-16 01:00 NZDT")

    # ------------------------------------------------------------------ #
    # resolve
    # ------------------------------------------------------------------ #
    def test_resolve_first_truthy(self):
        self.assertEqual(
            tz.resolve(["", None, "Pacific/Auckland", "America/Toronto"]),
            "Pacific/Auckland",
        )

    def test_resolve_empty_returns_default(self):
        self.assertEqual(tz.resolve([]), tz.DEFAULT_TZ)
        self.assertEqual(tz.resolve([None, "", None]), tz.DEFAULT_TZ)

    def test_resolve_custom_fallback(self):
        self.assertEqual(tz.resolve([], fallback="Pacific/Auckland"), "Pacific/Auckland")

    def test_resolve_validate_skips_invalid(self):
        """validate=True skips names that are not loadable timezones."""
        self.assertEqual(
            tz.resolve(["Bogus/Zone", "America/Toronto"], validate=True),
            "America/Toronto",
        )

    def test_resolve_validate_all_invalid_returns_fallback(self):
        self.assertEqual(
            tz.resolve(["Bogus/Zone", "Nope/Nowhere"], fallback="UTC", validate=True),
            "UTC",
        )

    def test_resolve_no_validate_returns_first_truthy_even_if_invalid(self):
        """Without validation, the first truthy candidate wins verbatim."""
        self.assertEqual(tz.resolve(["Bogus/Zone", "America/Toronto"]), "Bogus/Zone")

    # ------------------------------------------------------------------ #
    # format_dual
    # ------------------------------------------------------------------ #
    def test_format_dual_same_tz_single_string(self):
        dt = datetime(2026, 5, 20, 13, 40, 0)
        self.assertEqual(
            tz.format_dual(dt, "America/Toronto", "America/Toronto"),
            "2026-05-20 09:40 EDT",
        )

    def test_format_dual_different_tz_parenthetical(self):
        dt = datetime(2026, 5, 20, 13, 40, 0)
        self.assertEqual(
            tz.format_dual(dt, "America/Toronto", "Pacific/Auckland"),
            "2026-05-20 09:40 EDT (Auckland: 2026-05-21 01:40 NZST)",
        )

    def test_format_dual_day_rollover(self):
        """Secondary tz lands on the next calendar day."""
        dt = datetime(2026, 5, 20, 13, 40, 0)
        out = tz.format_dual(dt, "America/Toronto", "Pacific/Auckland")
        self.assertIn("2026-05-20", out)   # primary day
        self.assertIn("2026-05-21", out)   # secondary day +1
