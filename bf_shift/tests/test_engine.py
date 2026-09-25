"""The rules themselves, without the ORM."""

from datetime import date, datetime as D, timedelta

from odoo.tests import BaseCase, tagged

from ..lib import benefits, engine
from ..lib.engine import Params, PremiumRule, Segment

LNT = Params()


def day_shift(i, start=8, hours=8.0, brk=0.0, **kw):
    """A shift on 2026-09-06 (a Sunday) + i days."""
    s = D(2026, 9, 6, start) + timedelta(days=i)
    return Segment(("d", i, start), s, s + timedelta(hours=hours + brk), break_hours=brk, **kw)


def by_code(lines):
    out = {}
    for line in lines:
        out.setdefault(line.code, 0.0)
        out[line.code] += line.hours
    return out


@tagged("post_install", "-at_install")
class TestFloor(BaseCase):

    def test_agreement_can_raise_not_lower(self):
        eff, below = engine.most_favourable(Params(
            callback_min_hours=2.0, notice_days=3.0, ot_weekly_threshold=45.0,
            weekly_rest_hours=24.0, max_weekly=60.0, ot_multiplier=1.25))
        self.assertEqual(eff.callback_min_hours, 3.0)
        self.assertEqual(eff.notice_days, 5.0)
        self.assertEqual(eff.ot_weekly_threshold, 40.0)
        self.assertEqual(eff.weekly_rest_hours, 32.0)
        self.assertEqual(eff.max_weekly, 50.0)
        self.assertEqual(eff.ot_multiplier, 1.5)
        self.assertEqual(set(below), {"callback_min_hours", "notice_days", "ot_weekly_threshold",
                                      "weekly_rest_hours", "max_weekly", "ot_multiplier"})

    def test_better_values_kept(self):
        eff, below = engine.most_favourable(Params(
            callback_min_hours=4.0, notice_days=7.0, ot_weekly_threshold=37.5))
        self.assertEqual((eff.callback_min_hours, eff.notice_days, eff.ot_weekly_threshold),
                         (4.0, 7.0, 37.5))
        self.assertFalse(below)


@tagged("post_install", "-at_install")
class TestOvertime(BaseCase):

    def test_weekly_threshold(self):
        segs = [day_shift(i, hours=9) for i in range(1, 6)]  # Mon-Fri, 45 h
        lines = engine.compute_pay(segs, LNT, hourly_rate=20.0)
        codes = by_code(lines)
        self.assertEqual(codes["REG"], 40.0)
        self.assertEqual(codes["OT"], 5.0)
        ot = next(l for l in lines if l.code == "OT")
        self.assertEqual(ot.amount, 150.0)
        # The overtime is the last hours of the week.
        self.assertEqual(ot.keys, {("d", 5, 8)})

    def test_break_is_not_paid(self):
        segs = [day_shift(1, hours=8, brk=0.5)]
        self.assertEqual(by_code(engine.compute_pay(segs, LNT))["REG"], 8.0)
        segs = [day_shift(1, hours=8, brk=0.5, break_paid=True)]
        self.assertEqual(by_code(engine.compute_pay(segs, LNT))["REG"], 8.5)

    def test_week_boundary(self):
        # Sunday-started weeks: 40 h Mon-Fri, then 8 h the next Sunday: no overtime.
        segs = [day_shift(i) for i in range(1, 6)] + [day_shift(7)]
        codes = by_code(engine.compute_pay(segs, LNT))
        self.assertEqual(codes["REG"], 48.0)
        self.assertNotIn("OT", codes)
        # A Monday-started week puts that Sunday in the first week.
        codes = by_code(engine.compute_pay(segs, Params(week_start=0)))
        self.assertEqual(codes["OT"], 8.0)

    def test_leave_counts_toward_week(self):
        segs = [day_shift(1, kind="leave")] + [day_shift(i) for i in range(2, 7)]
        codes = by_code(engine.compute_pay(segs, LNT))
        self.assertEqual(codes["LEAVE"], 8.0)
        self.assertEqual(codes["REG"], 32.0)
        self.assertEqual(codes["OT"], 8.0)

    def test_daily_threshold_no_pyramiding(self):
        p = Params(ot_daily_threshold=8.0)
        segs = [day_shift(i, hours=10) for i in range(1, 5)]  # 4 x 10 h = 40 h
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["REG"], 32.0)
        self.assertEqual(codes["OT"], 8.0)
        # A fifth day of 10 h: 8 regular (32+8=40), 2 daily overtime.
        segs.append(day_shift(5, hours=10))
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["REG"], 40.0)
        self.assertEqual(codes["OT"], 10.0)

    def test_double_time_threshold(self):
        p = Params(dt_weekly_threshold=48.0)
        segs = [day_shift(i, hours=10) for i in range(1, 6)]  # 50 h
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["REG"], 40.0)
        self.assertEqual(codes["OT"], 8.0)
        self.assertEqual(codes["DT"], 2.0)

    def test_double_time_weekday(self):
        p = Params(dt_weekdays=frozenset({6}))  # Sunday overtime at double time
        segs = [day_shift(i) for i in range(1, 6)] + [day_shift(6, hours=4)]  # Sat 4 h OT
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["OT"], 4.0)
        self.assertNotIn("DT", codes)
        p = Params(dt_weekdays=frozenset({5}))  # Saturday
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["DT"], 4.0)

    def test_seventh_day(self):
        p = Params(dt_seventh_day=True)
        segs = [day_shift(i, hours=5) for i in range(0, 7)]  # 7 days x 5 h = 35 h
        codes = by_code(engine.compute_pay(segs, p))
        self.assertEqual(codes["REG"], 30.0)
        self.assertEqual(codes["DT"], 5.0)

    def test_bank(self):
        segs = [day_shift(i, hours=9, to_bank=(i == 5)) for i in range(1, 6)]
        lines = engine.compute_pay(segs, LNT, hourly_rate=20.0)
        codes = by_code(lines)
        self.assertNotIn("OT", codes)
        self.assertEqual(codes["BANKIN"], 7.5)  # 5 h x 1.5
        bank = next(l for l in lines if l.code == "BANKIN")
        self.assertEqual(bank.amount, 0.0)

    def test_bank_leave_and_on_call(self):
        segs = [day_shift(1, kind="bank_leave", hours=4), day_shift(2, kind="on_call", hours=12)]
        codes = by_code(engine.compute_pay(segs, LNT, hourly_rate=20.0))
        self.assertEqual(codes, {"BANKOUT": 4.0, "ONCALL": 12.0})

    def test_callback_minimum(self):
        segs = [day_shift(1, start=20, hours=1, kind="callback")]
        codes = by_code(engine.compute_pay(segs, LNT))
        self.assertEqual(codes["REG"], 1.0)
        self.assertEqual(codes["CBTOP"], 2.0)
        # An agreement with 4 hours.
        codes = by_code(engine.compute_pay(segs, Params(callback_min_hours=4.0)))
        self.assertEqual(codes["CBTOP"], 3.0)
        # Below the floor: raised back to 3.
        codes = by_code(engine.compute_pay(segs, Params(callback_min_hours=2.0)))
        self.assertEqual(codes["CBTOP"], 2.0)

    def test_emit_range_keeps_the_whole_week(self):
        segs = [day_shift(i, hours=9) for i in range(1, 6)]
        codes = by_code(engine.compute_pay(segs, LNT, emit_from=date(2026, 9, 11),
                                           emit_to=date(2026, 9, 11)))
        # Only Friday is emitted, but the week is counted: Friday = 4 REG + 5 OT.
        self.assertEqual(codes, {"REG": 4.0, "OT": 5.0})


@tagged("post_install", "-at_install")
class TestPremiums(BaseCase):

    EVE = PremiumRule("EVE", hour_from=16, hour_to=24, percent=7.0, floor=2.08)
    NIGHT = PremiumRule("NIGHT", hour_from=0, hour_to=8, percent=14.0, floor=4.17)

    def test_evening_share(self):
        seg = day_shift(1, start=12, hours=8)  # 12:00-20:00, 4 h in the window
        self.assertAlmostEqual(engine.premium_hours(seg, self.EVE), 4.0)

    def test_wrapping_window_and_break(self):
        wrap = PremiumRule("N", hour_from=23, hour_to=7, percent=10)
        seg = day_shift(1, start=23, hours=7.5, brk=0.5)  # 23:00-07:00, 8 h span
        self.assertAlmostEqual(engine.premium_hours(seg, wrap), 7.5)

    def test_percent_and_floor(self):
        self.assertAlmostEqual(engine.premium_per_hour(self.EVE, 20.0), 2.08)  # 1.40 < floor
        self.assertAlmostEqual(engine.premium_per_hour(self.EVE, 40.0), 2.80)
        self.assertAlmostEqual(engine.premium_per_hour(self.EVE, 0.0), 2.08)

    def test_majority_rule(self):
        rule = PremiumRule("EVE", hour_from=16, hour_to=24, percent=7.0, majority=True)
        mostly = day_shift(1, start=15, hours=8)  # 15-23: 7 of 8 h in window
        barely = day_shift(1, start=9, hours=8)  # 9-17: 1 of 8 h
        self.assertEqual(engine.premium_hours(mostly, rule), 8.0)
        self.assertEqual(engine.premium_hours(barely, rule), 0.0)

    def test_weekend_and_holiday(self):
        wkd = PremiumRule("WKD", applies_on="weekdays", weekdays=frozenset({5, 6}), percent=5)
        fri_night = day_shift(5, start=22, hours=8)  # Fri 22:00 -> Sat 06:00
        self.assertAlmostEqual(engine.premium_hours(fri_night, wkd), 6.0)
        hol = PremiumRule("HOL", applies_on="holidays", percent=100)
        seg = day_shift(1)
        self.assertEqual(engine.premium_hours(seg, hol, holidays=frozenset({seg.day})), 8.0)
        self.assertEqual(engine.premium_hours(seg, hol, holidays=frozenset()), 0.0)

    def test_dates_in_force(self):
        rule = PremiumRule("EVE", hour_from=16, hour_to=24, percent=7,
                           date_from=date(2026, 9, 9))
        self.assertEqual(engine.premium_hours(day_shift(1, start=16), rule), 0.0)
        self.assertEqual(engine.premium_hours(day_shift(3, start=16), rule), 8.0)

    def test_enhanced_rate(self):
        rule = PremiumRule("EVE", hour_from=16, hour_to=24, percent=7, enhanced_percent=10,
                           enhanced_threshold=70, enhanced_period_days=14)
        few = [day_shift(i, start=16) for i in range(1, 4)]  # 24 h
        lines = engine.compute_pay(few, LNT, [rule], hourly_rate=100, emit_from=date(2026, 9, 6))
        eve = next(l for l in lines if l.code == "EVE")
        self.assertFalse(eve.enhanced)
        self.assertAlmostEqual(eve.amount, 24 * 7.0)
        many = [day_shift(i, start=16) for i in range(0, 14) if i % 7 not in (0, 6)]  # 80 h
        lines = engine.compute_pay(many, LNT, [rule], hourly_rate=100, emit_from=date(2026, 9, 6))
        eve = next(l for l in lines if l.code == "EVE")
        self.assertTrue(eve.enhanced)
        self.assertAlmostEqual(eve.amount, 80 * 10.0)

    def test_premium_not_in_overtime_rate(self):
        segs = [day_shift(i, start=14, hours=9) for i in range(1, 6)]
        lines = engine.compute_pay(segs, LNT, [self.EVE], hourly_rate=40.0)
        ot = next(l for l in lines if l.code == "OT")
        self.assertEqual(ot.amount, 5 * 40.0 * 1.5)

    def test_holiday_indemnity(self):
        # 4 weeks of 40 h before the week of Monday 2026-10-12.
        segs = []
        for w in range(4):
            for d in range(1, 6):
                s = D(2026, 9, 13, 8) + timedelta(days=7 * w + d)
                segs.append(Segment((w, d), s, s + timedelta(hours=9)))  # 45 h a week
        hol = date(2026, 10, 12)
        # Overtime excluded: 4 x 40 / 20 = 8 h.
        self.assertEqual(engine.holiday_indemnity_hours(segs, LNT, hol), 8.0)
        lines = engine.compute_pay(segs, LNT, holidays=frozenset({hol}), hourly_rate=20,
                                   emit_from=hol, emit_to=hol)
        self.assertEqual(by_code(lines)["HOLIND"], 8.0)


@tagged("post_install", "-at_install")
class TestChecks(BaseCase):

    def codes(self, segs, **kw):
        return sorted(w.code for w in engine.check_segments(segs, kw.pop("params", LNT), **kw))

    def test_clean_week(self):
        segs = [day_shift(i, brk=0.5) for i in range(1, 6)]
        self.assertEqual(self.codes(segs), [])

    def test_notice(self):
        seg = day_shift(3, brk=0.5, informed_at=D(2026, 9, 6, 9))  # 4 days 23 h ahead
        self.assertEqual(self.codes([seg]), ["notice"])
        seg.informed_at = D(2026, 9, 4, 8)
        self.assertEqual(self.codes([seg]), [])
        seg.informed_at = D(2026, 9, 8, 8)
        self.assertEqual(self.codes([seg], availability_required=True), [])
        self.assertEqual(self.codes([seg], params=Params(notice_days=7.0)), ["notice"])

    def test_meal(self):
        self.assertEqual(self.codes([day_shift(1, hours=6)]), ["meal"])
        self.assertEqual(self.codes([day_shift(1, hours=5)]), [])
        self.assertEqual(self.codes([day_shift(1, hours=6, brk=0.5)]), [])

    def test_long_day_and_24_hours(self):
        self.assertEqual(self.codes([day_shift(1, hours=10, brk=0.5)]), [])
        self.assertEqual(self.codes([day_shift(1, hours=11, brk=0.5)]), ["daily_extra"])
        # Usual day of 7 h: 9.5 h is beyond 7 + 2.
        self.assertEqual(self.codes([day_shift(1, hours=9.5, brk=0.5)], usual_day_hours=7.0),
                         ["daily_extra"])
        segs = [day_shift(1, start=8, hours=8, brk=0.5), day_shift(1, start=18, hours=6.5, brk=0.5)]
        self.assertIn("max_24h", self.codes(segs, usual_day_hours=16))
        segs = [day_shift(1, start=8, hours=6, brk=0.5), day_shift(1, start=16, hours=6.5, brk=0.5)]
        self.assertNotIn("max_24h", self.codes(segs, usual_day_hours=16))
        self.assertIn("max_24h", self.codes(segs, usual_day_hours=16, flexible=True))

    def test_week(self):
        segs = [day_shift(i, hours=10.5, brk=0.5) for i in range(1, 6)]  # 52.5 h
        self.assertIn("weekly_max", self.codes(segs, usual_day_hours=10))
        rest = [day_shift(i, start=8, hours=12, brk=0.5) for i in range(0, 7)]
        self.assertIn("weekly_rest", self.codes(rest, usual_day_hours=12))
        # 6 days of 8 h leave a 40 h rest on the Saturday + Sunday edges: fine.
        ok = [day_shift(i, brk=0.5) for i in range(1, 7)]
        self.assertNotIn("weekly_rest", self.codes(ok))

    def test_overlap_and_rest_between(self):
        a = day_shift(1, start=8, hours=8, brk=0.5)
        b = day_shift(1, start=15, hours=4)
        self.assertIn("overlap", self.codes([a, b], usual_day_hours=12))
        c = day_shift(1, start=22, hours=4)
        self.assertNotIn("rest_between", self.codes([a, c], usual_day_hours=16))
        self.assertIn("rest_between", self.codes([a, c], usual_day_hours=16,
                                                 params=Params(min_rest_between=8.0)))

    def test_unavailable_and_only_keys(self):
        seg = day_shift(1, brk=0.5)
        busy = [(D(2026, 9, 7, 0), D(2026, 9, 8, 0))]
        self.assertEqual(self.codes([seg], unavailable=busy), ["unavailable"])
        self.assertEqual(self.codes([seg], unavailable=busy, only_keys=set()), [])


@tagged("post_install", "-at_install")
class TestRanking(BaseCase):

    def test_orders(self):
        a = engine.Candidate("a", date(2010, 1, 1), hours_offered=20, tie=1)
        b = engine.Candidate("b", date(2015, 1, 1), hours_offered=0, tie=2)
        c = engine.Candidate("c", date(2020, 1, 1), hours_offered=8, tie=3)
        keys = lambda m: [x.key for x in engine.rank_candidates([c, a, b], m)]
        self.assertEqual(keys("seniority"), ["a", "b", "c"])
        self.assertEqual(keys("inverse_seniority"), ["c", "b", "a"])
        self.assertEqual(keys("rotation"), ["b", "c", "a"])

    def test_missing_seniority_goes_last(self):
        a = engine.Candidate("a", None, tie=1)
        b = engine.Candidate("b", date(2020, 1, 1), tie=2)
        self.assertEqual([x.key for x in engine.rank_candidates([a, b])], ["b", "a"])


@tagged("post_install", "-at_install")
class TestBenefits(BaseCase):

    def test_overtime_meal(self):
        ok = benefits.classify("overtime_meal", amount=20, overtime_hours=2.5,
                               employer_requested=True, has_receipt=True)
        self.assertEqual((ok.quebec, ok.federal), ("not_taxable", "not_taxable"))
        # No receipt: taxable in Québec only.
        v = benefits.classify("overtime_meal", amount=20, overtime_hours=2.5,
                              employer_requested=True, has_receipt=False)
        self.assertEqual((v.quebec, v.federal), ("taxable", "not_taxable"))
        self.assertIn("no_receipt", v.reasons)
        # Third time this week: taxable in both.
        v = benefits.classify("overtime_meal", amount=20, overtime_hours=2.5,
                              employer_requested=True, has_receipt=True, times_this_week=3)
        self.assertEqual((v.quebec, v.federal), ("taxable", "taxable"))
        # Above the CRA limit.
        v = benefits.classify("overtime_meal", amount=30, overtime_hours=2.5,
                              employer_requested=True, has_receipt=True)
        self.assertEqual(v.federal, "taxable")
        # Under 2 hours.
        v = benefits.classify("overtime_meal", amount=10, overtime_hours=1.5,
                              employer_requested=True, has_receipt=True)
        self.assertEqual((v.quebec, v.federal), ("taxable", "taxable"))

    def test_other_kinds(self):
        v = benefits.classify("taxi", overtime_hours=3, employer_requested=True,
                              has_receipt=True, no_transit_or_safety=True)
        self.assertEqual((v.quebec, v.federal), ("not_taxable", "check"))
        self.assertEqual(benefits.classify("parking").quebec, "taxable")
        self.assertEqual(benefits.classify("parking", parking_exception=True).federal,
                         "not_taxable")
        self.assertEqual(benefits.classify("uniform").quebec, "not_taxable")
        v = benefits.classify("subsidized_meal")
        self.assertEqual((v.quebec, v.federal), ("check", "not_taxable"))


@tagged("post_install", "-at_install")
class TestDaylightSaving(BaseCase):
    """Montréal: back one hour on 2026-11-01, forward one hour on 2027-03-14."""

    def setUp(self):
        super().setUp()
        import pytz
        self.tz = pytz.timezone("America/Toronto")

    def test_night_of_the_change_is_paid_in_real_hours(self):
        autumn = Segment("a", D(2026, 10, 31, 22), D(2026, 11, 1, 6), tz=self.tz)
        spring = Segment("s", D(2027, 3, 13, 22), D(2027, 3, 14, 6), tz=self.tz)
        self.assertEqual(autumn.paid_hours, 9.0)
        self.assertEqual(spring.paid_hours, 7.0)
        self.assertEqual(by_code(engine.compute_pay([autumn], LNT))["REG"], 9.0)
        self.assertEqual(by_code(engine.compute_pay([spring], LNT))["REG"], 7.0)

    def test_premium_window_in_real_hours(self):
        night = PremiumRule("N", hour_from=0, hour_to=8, percent=14)
        autumn = Segment("a", D(2026, 10, 31, 22), D(2026, 11, 1, 6), tz=self.tz)
        spring = Segment("s", D(2027, 3, 13, 22), D(2027, 3, 14, 6), tz=self.tz)
        self.assertEqual(engine.premium_hours(autumn, night), 7.0)
        self.assertEqual(engine.premium_hours(spring, night), 5.0)

    def test_checks_count_real_hours(self):
        # 22:00 -> 12:00 the next day is 14 wall-clock hours, 15 real ones in autumn.
        long = Segment("a", D(2026, 10, 31, 22), D(2026, 11, 1, 12), break_hours=0.5, tz=self.tz)
        codes = [w.code for w in engine.check_segments([long], LNT, usual_day_hours=20)]
        self.assertIn("max_24h", codes)

    def test_without_time_zone_the_clock_is_used(self):
        seg = Segment("a", D(2026, 10, 31, 22), D(2026, 11, 1, 6))
        self.assertEqual(seg.paid_hours, 8.0)
