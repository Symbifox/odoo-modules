from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from odoo.tests import BaseCase

from ..models import engine
from ..models.engine import Item

TZ = ZoneInfo("America/Montreal")  # UTC-4 in October


def at(h, m=0, d=9):
    """Naive UTC datetime for a Montreal wall-clock time on 2026-10-d."""
    return datetime(2026, 10, d, h, m) + timedelta(hours=4)


class TestEngine(BaseCase):

    def test_points_are_widened_backwards(self):
        spans = engine.to_spans([Item(at(10), at(10), "gen", "point")])
        self.assertEqual(spans, [(at(10) - timedelta(minutes=2), at(10), "point")])
        spans = engine.to_spans([Item(at(10), at(10), "email", "point")], {"email": 5})
        self.assertEqual(spans[0][0], at(9, 55))

    def test_a_minute_counts_once(self):
        """Two traces filling the same minutes (two agents, a meeting and a call) count once."""
        items = [Item(at(9), at(10), "calendar", "meeting"), Item(at(9, 30), at(10, 30), "presence", "span")]
        figures = engine.day_figures(items, date(2026, 10, 9), TZ, gap_minutes=0)
        self.assertAlmostEqual(figures["hours"], 1.5)

    def test_gap_joins_and_band(self):
        items = [Item(at(9), at(9), "gen", "point"), Item(at(9, 20), at(9, 20), "gen", "point")]
        figures = engine.day_figures(items, date(2026, 10, 9), TZ, gap_minutes=15)
        # 8:58-9:00 and 9:18-9:20 are 18 minutes apart: two stretches at 15 min, one at 30.
        self.assertAlmostEqual(figures["hours"], 4 / 60)
        self.assertAlmostEqual(figures["hours_low"], 4 / 60)
        self.assertAlmostEqual(figures["hours_high"], 22 / 60)
        self.assertEqual(figures["session_count"], 2)

    def test_cut_at_local_midnight(self):
        """23:30 to 00:30 Montreal time: half an hour on each local day, not on UTC days."""
        items = [Item(at(23, 30), at(23, 30) + timedelta(hours=1), "presence", "span")]
        self.assertAlmostEqual(engine.day_figures(items, date(2026, 10, 9), TZ)["hours"], 0.5)
        self.assertAlmostEqual(engine.day_figures(items, date(2026, 10, 10), TZ)["hours"], 0.5)

    def test_outside_hours(self):
        items = [Item(at(8), at(10), "presence", "span")]
        schedule = [(at(9), at(17))]
        figures = engine.day_figures(items, date(2026, 10, 9), TZ, schedule=schedule)
        self.assertAlmostEqual(figures["outside_hours"], 1.0)
        self.assertNotIn("outside_hours", engine.day_figures(items, date(2026, 10, 9), TZ))

    def test_back_to_back_counts_joins_not_conflicts(self):
        meetings = [(at(9), at(10)), (at(10), at(11)), (at(11, 5), at(12)), (at(11, 30), at(12, 30)),
                    (at(14), at(15))]
        # 10:00 touches 10:00 (join), 11:05 is 5 min after 11:00 (join), 11:30 overlaps (conflict),
        # 14:00 is 1 h 30 after 12:30 (a real break).
        self.assertEqual(engine.back_to_back(meetings), 2)

    def test_meetings_figures(self):
        items = [Item(at(9), at(10), "calendar", "meeting"), Item(at(10), at(11), "calendar", "meeting")]
        figures = engine.day_figures(items, date(2026, 10, 9), TZ, gap_minutes=15)
        self.assertAlmostEqual(figures["meeting_hours"], 2.0)
        self.assertEqual(figures["back_to_back_meetings"], 1)
        self.assertAlmostEqual(figures["longest_session_hours"], 2.0)
        self.assertAlmostEqual(figures["span_hours"], 2.0)

    def test_source_hours(self):
        items = [Item(at(9), at(10), "calendar", "meeting"), Item(at(9, 30), at(10, 30), "presence", "span")]
        self.assertEqual(engine.source_hours(items, date(2026, 10, 9), TZ), {"calendar": 1.0, "presence": 1.0})

    def test_the_same_meeting_from_two_sources(self):
        """🟡 Calendar and meeting report give the same meeting: one join, not two, not none."""
        meetings = [(at(9), at(10)), (at(9), at(10)), (at(10), at(11))]
        self.assertEqual(engine.back_to_back(meetings), 1)
