"""The rules changed in 18.0.1.2.0 after the legal and tax review of
September 2026, without the ORM. Same helpers as test_engine."""

from dataclasses import replace
from datetime import date, datetime as D, timedelta

from odoo.tests import BaseCase, tagged

from ..lib import benefits, engine
from ..lib.engine import Params, Segment
from .test_engine import by_code, day_shift

LNT = Params()


def codes(segs, **kw):
    return sorted(w.code for w in engine.check_segments(segs, kw.pop("params", LNT), **kw))


@tagged("post_install", "-at_install")
class TestUsualDay(BaseCase):
    """Art. 59.0.1, 1st paragraph."""

    # The CNESST's example of a regular schedule: 8 h Monday, 7 h Tuesday,
    # 10 h Friday, every week. 2026-09-06 + i days: i = 1 Monday, 2 Tuesday, 5 Friday.
    CNESST = staticmethod(lambda day: {0: 8.0, 1: 7.0, 4: 10.0}.get(day.weekday(), 8.0))

    def test_usual_hours_read_by_weekday(self):
        tuesday = day_shift(2, hours=9.5, brk=0.5)
        self.assertEqual(codes([tuesday], usual_day_hours=self.CNESST), ["daily_extra"],
                         "7 h on Tuesday: 9.5 h is beyond 7 + 2")
        friday = day_shift(5, hours=11.5, brk=0.5)
        self.assertEqual(codes([friday], usual_day_hours=self.CNESST), [],
                         "10 h on Friday: 11.5 h is within 10 + 2")
        # The average of the week (8.33 h) would have missed Tuesday.
        self.assertEqual(codes([tuesday], usual_day_hours=25 / 3), [])

    def test_variable_hours_keep_only_twelve(self):
        long_day = day_shift(1, hours=11.5, brk=0.5)
        self.assertEqual(codes([long_day]), ["daily_extra"])
        self.assertEqual(codes([long_day], flexible=True), [], "no '+2 h' for variable hours")
        longer = day_shift(1, hours=12.5, brk=0.5)
        self.assertEqual(codes([longer], flexible=True), ["max_24h"])
        w = engine.check_segments([longer], LNT, flexible=True)[0]
        self.assertEqual((w.values["limit"], w.values["variable"]), (12.0, True))

    def test_split_shift_is_non_continuous(self):
        # 6:00-11:00 and 14:00-19:00: 10 h in two pieces, usual day 6 h.
        split = [day_shift(2, start=6, hours=5), day_shift(2, start=14, hours=5)]
        self.assertEqual(engine.split_days(split, LNT), {split[0].day})
        self.assertEqual(codes(split, usual_day_hours=6), [], "no '+2 h' on a split day")
        # 12.5 h in two pieces: beyond 12, although under 14.
        longer = [day_shift(2, start=6, hours=6), day_shift(2, start=14, hours=6.5)]
        self.assertIn("max_24h", codes(longer, usual_day_hours=6))
        # A gap under the threshold is a break, not a split.
        joined = [day_shift(2, start=6, hours=5),
                  Segment("j", D(2026, 9, 8, 11, 30), D(2026, 9, 8, 16, 30))]
        self.assertFalse(engine.split_days(joined, LNT))
        self.assertEqual(codes(joined, usual_day_hours=6), ["daily_extra"])
        self.assertTrue(engine.split_days(joined, Params(split_gap_hours=0.5)))

    def test_within_first_paragraph(self):
        seg = day_shift(1, hours=9, brk=0.5)
        self.assertTrue(engine.within_first_paragraph([seg], seg.key, LNT, 8.0))
        seg = day_shift(1, hours=10.5, brk=0.5)
        self.assertFalse(engine.within_first_paragraph([seg], seg.key, LNT, 8.0))
        self.assertTrue(engine.within_first_paragraph([seg], seg.key, LNT, 8.0, flexible=True))
        # 14 h in 24 counted across midnight, even on a usual day of 12 h.
        night = day_shift(1, start=20, hours=8)
        morning = day_shift(2, start=6, hours=7)
        self.assertFalse(engine.within_first_paragraph([night, morning], morning.key, LNT, 12.0))


@tagged("post_install", "-at_install")
class TestBenefitRules(BaseCase):
    """LI art. 37.0.3, CRA pages on meals and on transportation home,
    Revenu Québec letter 15-028003-001."""

    OT = dict(overtime_hours=2.5, employer_requested=True)

    def test_taxi_taxable_by_default_at_the_cra(self):
        v = benefits.classify("taxi", has_receipt=True, no_transit_or_safety=True, **self.OT)
        self.assertEqual((v.quebec, v.federal), ("not_taxable", "taxable"))
        self.assertEqual(v.reasons, ["cra_taxi"])
        v = benefits.classify("taxi", has_receipt=True, no_transit_or_safety=True,
                              federal_exemption=True, **self.OT)
        self.assertEqual((v.quebec, v.federal), ("not_taxable", "not_taxable"))
        self.assertEqual(v.reasons, ["federal_exemption"])

    def test_provided_needs_no_receipt(self):
        v = benefits.classify("overtime_meal", amount=20, provision="provided", **self.OT)
        self.assertEqual((v.quebec, v.federal), ("not_taxable", "not_taxable"))
        self.assertNotIn("no_receipt", v.reasons)
        v = benefits.classify("overtime_meal", amount=20, provision="reimbursed", **self.OT)
        self.assertEqual((v.quebec, v.federal), ("taxable", "not_taxable"))
        self.assertIn("no_receipt", v.reasons)
        v = benefits.classify("taxi", provision="provided", no_transit_or_safety=True, **self.OT)
        self.assertEqual(v.quebec, "not_taxable")
        v = benefits.classify("taxi", provision="reimbursed", no_transit_or_safety=True, **self.OT)
        self.assertEqual(v.quebec, "taxable")
        # The other conditions still hold for what is provided.
        v = benefits.classify("overtime_meal", amount=20, provision="provided",
                              overtime_hours=1.5, employer_requested=True)
        self.assertEqual(v.quebec, "taxable")
        self.assertIn("under_two_hours", v.reasons)

    def test_subsidized_meal(self):
        v = benefits.classify("subsidized_meal", meal_cost=9.0, meal_price_paid=9.0)
        self.assertEqual((v.quebec, v.federal, v.reasons),
                         ("not_taxable", "not_taxable", ["price_covers_cost"]))
        v = benefits.classify("subsidized_meal", meal_cost=9.0, meal_price_paid=10.0)
        self.assertEqual((v.quebec, v.federal), ("not_taxable", "not_taxable"))
        # A free meal is not "not taxable" at the CRA any more.
        v = benefits.classify("subsidized_meal", meal_cost=9.0, meal_price_paid=0.0)
        self.assertEqual((v.quebec, v.federal, v.reasons),
                         ("taxable", "taxable", ["price_below_cost"]))
        self.assertEqual(benefits.subsidized_value(9.0, 5.5), 3.5)
        self.assertEqual(benefits.subsidized_value(9.0, 12.0), 0.0)
        v = benefits.classify("subsidized_meal", meal_price_paid=4.0)
        self.assertEqual((v.quebec, v.federal, v.reasons), ("check", "check", ["meal_cost_missing"]))


def amounts(lines):
    out = {}
    for line in lines:
        out[line.code] = round(out.get(line.code, 0.0) + line.amount, 2)
    return out


@tagged("post_install", "-at_install")
class TestOnCallAndMinimum(BaseCase):
    """LNT art. 57 and 58: on call at home, and the 3-hour minimum."""

    def test_on_call_pay_is_the_agreements(self):
        # A full week of 40 h, plus a 12-hour on-call night: not worked time.
        segs = [day_shift(i) for i in range(1, 6)]
        segs.append(day_shift(5, start=18, hours=12, kind="on_call"))
        lines = engine.compute_pay(segs, LNT, hourly_rate=30.0)
        self.assertEqual(by_code(lines), {"REG": 40.0, "ONCALL": 12.0})
        self.assertEqual(amounts(lines)["ONCALL"], 0.0, "the LNT requires no on-call pay")
        per_hour = Params(on_call_pay="per_hour", on_call_amount=2.5)
        lines = engine.compute_pay(segs, per_hour, hourly_rate=30.0)
        self.assertEqual(amounts(lines)["ONCALL"], 30.0)
        self.assertNotIn("OT", by_code(lines), "on-call hours never make overtime")
        flat = Params(on_call_pay="per_period", on_call_amount=50.0)
        segs.append(day_shift(6, start=18, hours=12, kind="on_call"))
        lines = engine.compute_pay(segs, flat, hourly_rate=30.0)
        self.assertEqual(amounts(lines)["ONCALL"], 100.0, "two periods")
        self.assertEqual(by_code(lines)["ONCALL"], 24.0)

    def test_short_regular_shift_gets_three_hours(self):
        short = [day_shift(1, hours=2)]
        lines = engine.compute_pay(short, LNT, hourly_rate=30.0)
        self.assertEqual(by_code(lines), {"REG": 2.0, "MINTOP": 1.0})
        self.assertEqual(amounts(lines)["MINTOP"], 30.0)
        self.assertEqual(by_code(engine.compute_pay([day_shift(1, hours=3)], LNT)), {"REG": 3.0})
        # Force majeure, and the two exceptions of art. 58.
        self.assertNotIn("MINTOP", by_code(engine.compute_pay(
            [day_shift(1, hours=2, force_majeure=True)], LNT)))
        for exception in ("multiple_presences", "usually_short"):
            self.assertNotIn("MINTOP", by_code(engine.compute_pay(
                short, Params(presence_exception=exception))))
        # An agreement may grant more; below 3 it is raised back.
        self.assertEqual(by_code(engine.compute_pay(short, Params(presence_min_hours=4)))["MINTOP"], 2.0)
        self.assertEqual(by_code(engine.compute_pay(short, Params(presence_min_hours=1)))["MINTOP"], 1.0)

    def test_one_presence_made_of_two_shifts(self):
        # 8:00-10:00 then 10:15-11:30: the person stays, one presence of 3.25 h.
        later = D(2026, 9, 7, 10, 15)
        joined = [day_shift(1, hours=2), Segment("b", later, later + timedelta(hours=1.25))]
        self.assertNotIn("MINTOP", by_code(engine.compute_pay(joined, LNT)))
        # 7:00-9:00 and 15:00-17:00: two presences, each topped up, even when
        # both segments carry the same key.
        apart = [day_shift(1, start=7, hours=2), day_shift(1, start=15, hours=2)]
        self.assertEqual(by_code(engine.compute_pay(apart, LNT))["MINTOP"], 2.0)
        same_key = [Segment("k", s.start, s.end) for s in apart]
        self.assertEqual(by_code(engine.compute_pay(same_key, LNT))["MINTOP"], 2.0)

    def test_remote_call_back(self):
        remote = [day_shift(1, start=22, hours=0.5, kind="callback", remote=True)]
        self.assertEqual(by_code(engine.compute_pay(remote, LNT)), {"REG": 0.5},
                         "nobody came to the workplace: no minimum of art. 58")
        granted = Params(remote_callback_minimum=True, callback_min_hours=4.0)
        self.assertEqual(by_code(engine.compute_pay(remote, granted))["CBTOP"], 3.5)
        on_site = [day_shift(1, start=22, hours=0.5, kind="callback")]
        self.assertEqual(by_code(engine.compute_pay(on_site, LNT))["CBTOP"], 2.5)
        self.assertNotIn("CBTOP", by_code(engine.compute_pay(
            [day_shift(1, start=22, hours=0.5, kind="callback", force_majeure=True)], LNT)))
        # Under an exception of art. 58, the agreement's call-back minimum is
        # applied as typed, without the 3-hour floor.
        eff, below = engine.most_favourable(Params(presence_exception="usually_short",
                                                   callback_min_hours=2.0))
        self.assertEqual((eff.callback_min_hours, below), (2.0, []))


def week(w, hours_per_day, days=5, start=8, **kw):
    """Monday to Friday (or fewer days) of week ``w`` after Sunday 2026-09-06."""
    out = []
    for d in range(1, days + 1):
        s = D(2026, 9, 6, start) + timedelta(days=7 * w + d)
        out.append(Segment(("w", w, d), s, s + timedelta(hours=hours_per_day), **kw))
    return out


AVG2 = Params(averaging_weeks=2, averaging_anchor=date(2026, 9, 6))


@tagged("post_install", "-at_install")
class TestAveraging(BaseCase):
    """LNT art. 53: overtime on the average of the averaging period."""

    def test_research_cases(self):
        # 44 h then 36 h over 2 weeks: the average is 40 h, no overtime.
        self.assertEqual(by_code(engine.compute_pay(week(0, 8.8) + week(1, 7.2), AVG2)),
                         {"REG": 80.0})
        # Without averaging, the first week pays 4 h of overtime.
        self.assertEqual(by_code(engine.compute_pay(week(0, 8.8) + week(1, 7.2), LNT))["OT"], 4.0)
        # 44 h and 44 h: 88 - 80 = 8 h.
        self.assertEqual(by_code(engine.compute_pay(week(0, 8.8) + week(1, 8.8), AVG2)),
                         {"REG": 80.0, "OT": 8.0})
        # CNESST example, 37 h + 47 h: 84 - 80 = 4 h, not 2.
        self.assertEqual(by_code(engine.compute_pay(week(0, 7.4) + week(1, 9.4), AVG2))["OT"], 4.0)
        # 30 h + 50 h: average of 40 h, nothing (the raised threshold would pay 6).
        self.assertNotIn("OT", by_code(engine.compute_pay(week(0, 6) + week(1, 10), AVG2)))
        # A threshold above 40 h is still brought back to 40 h: it is no way to average.
        self.assertEqual(by_code(engine.compute_pay(week(0, 8.8), Params(ot_weekly_threshold=44)))["OT"],
                         4.0)

    def test_periods_follow_the_anchor(self):
        self.assertEqual(engine.averaging_period(date(2026, 9, 16), AVG2),
                         (date(2026, 9, 6), date(2026, 9, 19)))
        self.assertEqual(engine.averaging_period(date(2026, 9, 22), AVG2),
                         (date(2026, 9, 20), date(2026, 10, 3)))
        self.assertEqual(engine.averaging_period(date(2026, 9, 5), AVG2),
                         (date(2026, 8, 23), date(2026, 9, 5)))
        # An anchor in mid-week starts the cycle on its pay week.
        mid = Params(averaging_weeks=2, averaging_anchor=date(2026, 9, 9))
        self.assertEqual(engine.averaging_period(date(2026, 9, 7), mid)[0], date(2026, 9, 6))
        self.assertEqual(engine.averaging_period(date(2026, 9, 7), LNT),
                         (date(2026, 9, 6), date(2026, 9, 12)))
        # Weeks 1-2 then 3-4: 44 + 36 then 44 + 44.
        segs = week(0, 8.8) + week(1, 7.2) + week(2, 8.8) + week(3, 8.8)
        self.assertEqual(by_code(engine.compute_pay(segs, AVG2))["OT"], 8.0)

    def test_pay_period_inside_an_averaging_period(self):
        segs = week(0, 8.8) + week(1, 8.8)
        first = engine.compute_pay(segs, AVG2, hourly_rate=20.0,
                                   emit_from=date(2026, 9, 6), emit_to=date(2026, 9, 12))
        self.assertEqual(by_code(first), {"REG": 44.0}, "usual rate until the last week")
        second = engine.compute_pay(segs, AVG2, hourly_rate=20.0,
                                    emit_from=date(2026, 9, 13), emit_to=date(2026, 9, 19))
        self.assertEqual(by_code(second), {"REG": 36.0, "OT": 8.0})
        self.assertEqual(amounts(second)["OT"], 8 * 20.0 * 1.5)
        # A 4-week period, paid by a period that holds only its last Thursday
        # and Friday: 32 h owed, 17.6 h there, the rest was paid at the usual
        # rate earlier and gets its premium only.
        avg4 = Params(averaging_weeks=4, averaging_anchor=date(2026, 9, 6))
        segs = [s for w in range(4) for s in week(w, 9.6)]
        lines = engine.compute_pay(segs, avg4, hourly_rate=20.0,
                                   emit_from=date(2026, 10, 1), emit_to=date(2026, 10, 3))
        self.assertEqual(by_code(lines), {"OT": 19.2, "OTAVG": 12.8})
        otavg = next(l for l in lines if l.code == "OTAVG")
        self.assertEqual((otavg.multiplier, otavg.amount), (0.5, 128.0))
        self.assertNotIn("OT", by_code(engine.compute_pay(
            segs, avg4, emit_from=date(2026, 9, 27), emit_to=date(2026, 9, 30))))

    def test_daily_threshold_and_bank(self):
        daily = replace(AVG2, ot_daily_threshold=8.0)
        # 0.8 h a day of daily overtime in week 1 (4 h); the period then holds
        # 76 regular hours: nothing more, and no hour is paid twice.
        self.assertEqual(by_code(engine.compute_pay(week(0, 8.8) + week(1, 7.2), daily)),
                         {"REG": 76.0, "OT": 4.0})
        # Banked shifts bank the averaging overtime too.
        segs = week(0, 8.8) + week(1, 8.8, to_bank=True)
        self.assertEqual(by_code(engine.compute_pay(segs, AVG2)), {"REG": 80.0, "BANKIN": 12.0})
        # Leave counts toward the period, as toward the week (art. 52).
        segs = week(0, 8.8) + week(1, 8.8, days=4) + [week(1, 8.8)[4]]
        segs[-1].kind = "leave"
        self.assertEqual(by_code(engine.compute_pay(segs, AVG2))["OT"], 8.0)

    def test_individual_agreement_cap(self):
        ind = replace(AVG2, averaging_week_cap=50.0)
        # 52 h then 28 h: the 2 h beyond 50 are overtime of that week, the
        # other 78 h average under 40.
        segs = week(0, 10.4) + week(1, 5.6)
        self.assertEqual(by_code(engine.compute_pay(segs, ind)), {"REG": 78.0, "OT": 2.0})
        self.assertEqual(by_code(engine.compute_pay(
            segs, ind, emit_from=date(2026, 9, 6), emit_to=date(2026, 9, 12))),
            {"REG": 50.0, "OT": 2.0})
        self.assertIn("averaging_week_cap", codes(segs, params=ind))
        self.assertNotIn("averaging_week_cap", codes(segs, params=AVG2))

    def test_weekly_warnings(self):
        # 55 h then 45 h: over 50 in the first week, 50 on average.
        segs = week(0, 11) + week(1, 9)
        self.assertIn("weekly_max", codes(segs))
        self.assertNotIn("weekly_max", codes(segs, params=AVG2))
        over = engine.check_segments(week(0, 11) + week(1, 11), AVG2)
        warn = next(w for w in over if w.code == "weekly_max")
        self.assertEqual(warn.values, {"hours": 55.0, "limit": 50.0, "weeks": 2,
                                       "first": "2026-09-06", "last": "2026-09-19"})
        # The weekly rest of art. 78 stays week by week.
        seven = [Segment("sun", D(2026, 9, 6, 8), D(2026, 9, 6, 12))] + week(0, 8, days=6)
        self.assertIn("weekly_rest", codes(seven, params=AVG2))
