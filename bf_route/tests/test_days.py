from datetime import date, datetime, timedelta

from unittest.mock import patch

from odoo.addons.bf_route.models.bf_route_day import clean_env
from odoo.addons.bf_route.models.tools import local_today
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import MONDAY, RouteCase


@tagged("post_install", "-at_install")
class TestDays(RouteCase):

    def test_runs_on_weekdays_and_interval(self):
        self.route.write({"wed": True, "interval_weeks": 2})
        self.assertTrue(self.route._runs_on(MONDAY))
        self.assertTrue(self.route._runs_on(MONDAY + timedelta(days=2)))
        self.assertFalse(self.route._runs_on(MONDAY + timedelta(days=1)))
        self.assertFalse(self.route._runs_on(MONDAY + timedelta(days=7)), "every other week")
        self.assertTrue(self.route._runs_on(MONDAY + timedelta(days=14)))
        self.assertFalse(self.route._runs_on(MONDAY - timedelta(days=7)), "before the start")
        self.route.date_end = MONDAY + timedelta(days=10)
        self.assertFalse(self.route._runs_on(MONDAY + timedelta(days=14)), "after the end")

    def test_generate_days_once_with_stops_and_local_start(self):
        created = self.route._generate_days(today=MONDAY)
        self.assertEqual(created.mapped("date"), [MONDAY, MONDAY + timedelta(days=7)])
        day = created.filtered(lambda d: d.date == MONDAY)
        self.assertEqual(day.user_id, self.worker)
        self.assertEqual(day.vehicle_id, self.truck)
        self.assertEqual(day.stop_ids.mapped("partner_id"), self.alice | self.bob | self.carol)
        # 8:00 in Toronto on 2026-10-12 (EDT, UTC-4) is 12:00 UTC.
        self.assertEqual(day.date_planned_start, datetime(2026, 10, 12, 12, 0))
        self.assertFalse(self.route._generate_days(today=MONDAY), "never twice")
        day.with_user(self.manager).action_cancel()
        self.assertFalse(self.route._generate_days(today=MONDAY), "a cancelled day is not recreated")

    def test_refresh_follows_the_template_until_the_office_edits_a_day(self):
        days = self.route._generate_days(today=MONDAY).sorted("date")
        untouched, edited = days
        self.assertFalse(untouched.edited or edited.edited, "days the calendar makes are not edited")
        extra = self.env["res.partner"].create({"name": "Extra"})
        self.env["bf.route.day.stop"].create({"day_id": edited.id, "partner_id": extra.id})
        self.assertTrue(edited.edited, "a stop added by hand edits the day")
        newcomer = self.env["res.partner"].create({"name": "Newcomer"})
        self.route.with_user(self.manager).write(
            {"stop_ids": [(0, 0, {"partner_id": newcomer.id, "sequence": 40})], "user_id": self.manager.id})
        self.assertIn(newcomer, untouched.stop_ids.partner_id)
        self.assertEqual(untouched.user_id, self.manager)
        self.assertNotIn(newcomer, edited.stop_ids.partner_id, "an edited day is never rewritten")
        self.assertEqual(edited.user_id, self.worker)
        self.assertIn(extra, edited.stop_ids.partner_id)
        self.assertIn("edited by hand", edited.message_ids[0].body)

    def test_office_changes_on_a_day_survive_a_route_change(self):
        day = self.route._generate_days(today=MONDAY).sorted("date")[0]
        day.with_user(self.manager).write({"user_id": self.other.id})
        self.route.with_user(self.manager).write({"start_hour": 6.0})
        self.assertEqual(day.user_id, self.other)

    def test_a_day_planned_by_hand_is_the_office_s(self):
        self.assertTrue(self.make_day().edited)

    def test_moved_day_is_not_made_again(self):
        day = self.route._generate_days(today=MONDAY).filtered(lambda d: d.date == MONDAY)
        day.with_user(self.manager).date = MONDAY + timedelta(days=1)
        self.assertFalse(self.route._generate_days(today=MONDAY), "the moved Monday is not made again")

    def test_new_weekdays_drop_the_untouched_days_only(self):
        days = self.route._generate_days(today=MONDAY).sorted("date")
        kept = days[1]
        kept.with_user(self.manager).write({"vehicle_id": False})
        self.route.with_user(self.manager).write({"mon": False, "tue": True})
        self.assertFalse(days[0].exists(), "the untouched Monday goes")
        self.assertTrue(kept.exists(), "the edited Monday stays")
        self.assertIn("no longer runs", kept.message_ids[0].body)

    def test_responsible_without_time_zone_uses_the_company_s(self):
        self.manager.tz = False
        self.company.partner_id.tz = "America/Toronto"
        day = self.route._generate_days(today=MONDAY).filtered(lambda d: d.date == MONDAY)
        self.assertEqual(day.date_planned_start, datetime(2026, 10, 12, 12, 0))
        self.assertEqual(self.route.with_user(self.manager)._tz().zone, "America/Toronto")

    def test_start_mark_finish(self):
        day = self.make_day()
        data = self.start(day, odometer=1000)
        self.assertEqual(data["state"], "in_progress")
        alice, bob, carol = day.stop_ids.sorted("sequence")
        alice.with_user(self.worker).app_mark("done", note="Left 3 jugs", app_key="k1")
        self.assertEqual(alice.state, "done")
        self.assertEqual(alice.done_by, self.worker)
        self.assertEqual(alice.note, "Left 3 jugs")
        bob.with_user(self.worker).app_mark("absent", app_key="k2")
        day.with_user(self.worker).app_finish(odometer=1085)
        self.assertEqual(day.state, "done")
        self.assertEqual(carol.state, "missed")
        self.assertAlmostEqual(day.distance_actual_km, 85.0)
        log = self.env["fleet.vehicle.odometer"].search([("vehicle_id", "=", self.truck.id)])
        self.assertEqual(log.mapped("value"), [1085.0], "the odometer is logged to the vehicle")
        self.truck.invalidate_recordset(["odometer"])
        self.assertEqual(self.truck.odometer, 1085.0)
        activity = day.activity_ids
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.user_id, self.manager)
        self.assertIn("Carol Clinic", activity.note)

    def test_same_key_counts_once(self):
        day = self.make_day()
        self.start(day)
        alice = day.stop_ids.sorted("sequence")[0]
        alice.with_user(self.worker).app_mark("done", note="first", app_key="same")
        alice.with_user(self.worker).app_mark("absent", note="resent", app_key="same")
        self.assertEqual(alice.state, "done")
        self.assertEqual(alice.note, "first")

    def test_phone_cannot_set_missed_nor_mark_before_start(self):
        day = self.make_day()
        alice = day.stop_ids.sorted("sequence")[0]
        with self.assertRaises(UserError):
            alice.with_user(self.worker).app_mark("done")
        self.start(day)
        for forbidden in ("missed", "todo", "anything"):
            with self.assertRaises(UserError):
                alice.with_user(self.worker).app_mark(forbidden)

    def test_postponed_moves_to_next_planned_day_once(self):
        days = self.route._generate_days(today=MONDAY)
        today, next_week = days.sorted("date")
        extra = self.env["res.partner"].create({"name": "Extra"})
        self.env["bf.route.day.stop"].create({"day_id": today.id, "partner_id": extra.id})
        self.start(today)
        today.stop_ids.filtered(lambda s: s.partner_id == extra).with_user(self.worker).app_mark("postponed")
        self.assertEqual(next_week.stop_ids.filtered(lambda s: s.partner_id == extra).origin, "postponed")
        self.assertFalse(next_week.edited, "an automatic copy is not an edit")
        # Bob is already on next week's day: not twice, a note instead.
        bob = today.stop_ids.filtered(lambda s: s.partner_id == self.bob)
        bob.with_user(self.worker).app_mark("postponed")
        self.assertEqual(len(next_week.stop_ids.filtered(lambda s: s.partner_id == self.bob)), 1)
        self.assertIn("already on this day", next_week.message_ids[0].body)
        self.assertTrue(next_week.edited, "the template must not rewrite what was added to it")

    def test_a_stop_is_marked_once(self):
        day = self.make_day()
        self.start(day)
        alice = day.stop_ids.sorted("sequence")[0]
        alice.with_user(self.worker).app_mark("done", app_key="first")
        with self.assertRaises(UserError):
            alice.with_user(self.worker).app_mark("absent", app_key="second")
        self.assertEqual(alice.state, "done")
        alice.with_user(self.worker).app_mark("done", app_key="first")  # the same mark sent again

    def test_odometer_is_bounded_without_a_start_reading(self):
        self.env["fleet.vehicle.odometer"].create({"vehicle_id": self.truck.id, "value": 1000})
        day = self.make_day()
        with self.assertRaises(UserError):
            self.start(day, odometer=1000 + 20000)
        self.start(day)
        with self.assertRaises(UserError):
            day.with_user(self.worker).app_finish(odometer=10_000_000)
        with self.assertRaises(UserError):
            day.with_user(self.worker).app_finish(odometer=2_999_999 + 2)

    def test_the_phone_cannot_slip_a_context_in(self):
        day = self.make_day().with_user(self.worker).with_context(
            tracking_disable=True, skip_invoice_sync=True, default_state="done", lang="en_US")
        self.assertEqual(clean_env(day).env.context, {"lang": "en_US"})
        seen = []
        original = type(day).action_start

        def spy(records, *args, **kwargs):
            seen.append(dict(records.env.context))
            return original(records, *args, **kwargs)

        with patch.object(type(day), "action_start", spy):
            day.app_start()
        self.assertNotIn("tracking_disable", seen[0])
        self.assertNotIn("skip_invoice_sync", seen[0])

    def test_a_refused_mark_reaches_the_office_once(self):
        day = self.make_day()
        self.start(day)
        alice = day.stop_ids.sorted("sequence")[0]
        Day = day.with_user(self.worker)
        Day.app_report_refusal("k1", stop_id=alice.id, label="Alice", reason="Already marked",
                               state="done", note='<img src="https://e.vil/p.png">')
        Day.app_report_refusal("k1", stop_id=alice.id, label="Alice", reason="Already marked")
        self.assertEqual(len(day.message_ids.filtered(lambda m: "Already marked" in (m.body or ""))), 1,
                         "the same key counts once")
        activity = day.activity_ids.filtered(lambda a: "Marks refused" in a.summary)
        self.assertEqual(activity.user_id, self.manager)
        self.assertNotIn("<img", activity.note, "what the worker typed is escaped")
        Day.app_report_refusal("k2", stop_id=alice.id, label="Alice", reason="Second one")
        self.assertEqual(len(day.activity_ids.filtered(lambda a: "Marks refused" in a.summary)), 1,
                         "one activity per day, completed")
        with self.assertRaises(AccessError):
            day.with_user(self.other).app_report_refusal("k3", reason="x")

    def test_a_refusal_for_a_deleted_stop_still_reaches_the_office(self):
        day = self.make_day()
        self.start(day)
        bob = day.stop_ids.filtered(lambda s: s.partner_id == self.bob)
        bob_id = bob.id
        bob.unlink()
        day.with_user(self.worker).app_report_refusal("k9", stop_id=bob_id, label="Bob Garage",
                                                      reason="Record does not exist")
        self.assertIn("Bob Garage (name sent by the phone, stop removed)", day.message_ids[0].body)

    def test_refusal_reports_are_capped_and_say_so(self):
        day = self.make_day()
        answers = [day.with_user(self.worker).app_report_refusal("flood%s" % n, reason="x")
                   for n in range(60)]
        self.assertEqual(len(day.message_ids.filtered(lambda m: "refused" in (m.body or ""))), 50)
        self.assertEqual(answers[49:51], [True, False], "past the cap, the phone is told")

    def test_a_refusal_key_is_one_token(self):
        day = self.make_day()
        self.assertFalse(day.with_user(self.worker).app_report_refusal("a b c d", reason="x"))
        self.assertFalse(day.refusal_keys)

    def test_customer_taken_off_the_route_leaves_the_days_that_follow_it(self):
        days = self.route._generate_days(today=MONDAY).sorted("date")
        follows, edited = days
        edited.with_user(self.manager).write({"vehicle_id": False})
        bob_template = self.route.stop_ids.filtered(lambda s: s.partner_id == self.bob)
        bob_template.with_user(self.manager).unlink()
        self.assertNotIn(self.bob, follows.stop_ids.partner_id)
        self.assertIn(self.bob, edited.stop_ids.partner_id)
        self.assertTrue(edited.activity_ids.filtered(lambda a: "taken off the route" in a.summary))
        self.route.with_user(self.manager).write({"mon": False, "tue": True})
        self.assertFalse(follows.exists(), "an untouched day of the old calendar goes")

    def test_a_context_made_up_by_the_phone_is_dropped(self):
        day = self.make_day().with_context(tz="Nowhere/Never", lang="xx_XX")
        self.assertEqual(clean_env(day).env.context, {})

    def test_a_day_with_a_postponed_stop_is_not_dropped(self):
        days = self.route._generate_days(today=MONDAY).sorted("date")
        extra = self.env["res.partner"].create({"name": "Extra"})
        self.env["bf.route.day.stop"].with_context(bf_route_auto=True).create(
            {"day_id": days[1].id, "partner_id": extra.id, "origin": "postponed"})
        self.assertFalse(days[1].edited)
        self.route.with_user(self.manager).write({"mon": False, "tue": True})
        self.assertTrue(days[1].exists(), "the postponed stop is not lost")
        self.assertFalse(days[0].exists())

    def test_only_a_planned_day_is_cancelled(self):
        day = self.make_day()
        self.start(day)
        with self.assertRaises(UserError):
            day.with_user(self.manager).action_cancel()

    def test_absurd_odometer_and_start_time_are_refused(self):
        day = self.make_day()
        self.start(day, odometer=1000, client_time="2001-01-01T08:00:00Z")
        self.assertGreater(day.date_started.year, 2001, "a start long before the day is not trusted")
        with self.assertRaises(UserError):
            day.with_user(self.worker).app_finish(odometer=1000 + 5000)

    def test_heavy_vehicle_needs_inspection(self):
        self.truck.bf_route_heavy = True
        day = self.make_day()
        with self.assertRaises(UserError):
            self.start(day)
        self.start(day, safety_check=True)
        self.assertEqual(day.safety_check_by, self.worker)

    def test_odometer_cannot_go_back(self):
        day = self.make_day()
        self.start(day, odometer=500)
        with self.assertRaises(UserError):
            day.with_user(self.worker).app_finish(odometer=400)

    def today(self):
        # The worker's day, as the phone sees it: in UTC it is already tomorrow from 20:00 in
        # Toronto, and a day made for the UTC date is not "today" for app_load.
        return local_today(self.env["bf.route"]._tz_for(self.worker))

    def test_phone_time_kept_when_sent_later(self):
        day = self.make_day(day=self.today())
        self.start(day, client_time=(datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z")
        alice = day.stop_ids.sorted("sequence")[0]
        made = datetime.utcnow() - timedelta(minutes=30)
        alice.with_user(self.worker).app_mark("done", client_time=made.isoformat() + "Z")
        self.assertTrue(alice.sent_later)
        self.assertAlmostEqual(alice.done_at, made.replace(microsecond=0), delta=timedelta(seconds=1))
        bob = day.stop_ids.sorted("sequence")[1]
        future = datetime.utcnow() + timedelta(hours=1)
        bob.with_user(self.worker).app_mark("done", client_time=future.isoformat() + "Z")
        self.assertLess(bob.done_at, future - timedelta(minutes=30), "a clock ahead is not trusted")
        self.assertFalse(bob.sent_later)

    def test_app_load_shows_my_day_only(self):
        today = self.today()
        self.route.write({"date_start": today - timedelta(days=today.weekday())})
        mine = self.make_day(day=today)
        data = self.env["bf.route.day"].with_user(self.worker).app_load()
        self.assertEqual([d["id"] for d in data["days"]], [mine.id])
        self.assertEqual(data["days"][0]["stops"][0]["partner"], "Alice Café")
        self.assertEqual(data["days"][0]["stops"][0]["window"], "09:00 – 11:00")
        self.assertFalse(self.env["bf.route.day"].with_user(self.other).app_load()["days"])
