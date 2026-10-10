from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MONDAY, RouteCase

REQUEST = "odoo.addons.bf_route.models.bf_route_engine.requests.request"


def answer(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


@tagged("post_install", "-at_install")
class TestWatch(RouteCase):

    def test_late_day_alerts_once(self):
        day = self.make_day()
        day.date_planned_start = fields.Datetime.now() - timedelta(minutes=45)
        self.env["bf.route.day"]._watch()
        self.env["bf.route.day"]._watch()
        self.assertEqual(len(day.activity_ids), 1)
        self.assertEqual(day.activity_ids.user_id, self.manager)
        self.assertIn("not started", day.activity_ids.summary)

    def test_alert_in_the_reader_s_language(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.manager.lang = "fr_CA"
        day = self.make_day()
        day.date_planned_start = fields.Datetime.now() - timedelta(minutes=45)
        self.env["bf.route.day"]._watch()
        self.assertIn("Route pas commencée", day.activity_ids.summary)

    def test_not_late_within_grace(self):
        day = self.make_day()
        day.date_planned_start = fields.Datetime.now() - timedelta(minutes=10)
        self.env["bf.route.day"]._watch()
        self.assertFalse(day.activity_ids)

    def test_day_left_open_alerts_once(self):
        day = self.make_day(day=fields.Date.today() - timedelta(days=2))
        self.start(day)
        self.env["bf.route.day"]._watch()
        self.env["bf.route.day"]._watch()
        self.assertEqual(len(day.activity_ids.filtered(lambda a: "left open" in a.summary)), 1)

    def test_cron_runs(self):
        self.env["bf.route"]._cron_watch()

    def test_one_failing_job_does_not_stop_the_others(self):
        day = self.make_day()
        day.date_planned_start = fields.Datetime.now() - timedelta(hours=2)
        with patch.object(type(self.env["bf.route.day.stop"]), "_purge_positions",
                          side_effect=ValueError("boom")):
            self.env["bf.route"]._cron_watch()
        self.assertEqual(len(day.activity_ids), 1, "the alerts ran despite the failing purge")


@tagged("post_install", "-at_install")
class TestEngine(RouteCase):

    def setUp(self):
        super().setUp()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("bf_route.osrm_url", "http://osrm:5000/")
        params.set_param("bf_route.vroom_url", "http://vroom:3000")

    def test_no_engine_says_so(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_route.osrm_url", "")
        with self.assertRaises(UserError):
            self.route.with_user(self.manager).action_compute_distance()

    def test_missing_coordinates_named(self):
        self.bob.partner_latitude = self.bob.partner_longitude = 0
        with self.assertRaisesRegex(UserError, "Bob Garage"):
            self.route.with_user(self.manager).action_compute_distance()

    def test_engine_down_is_a_user_error(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            with self.assertRaises(UserError):
                self.route.with_user(self.manager).action_compute_distance()

    def test_forged_answer_is_a_user_error(self):
        for forged in ({"code": "Ok", "routes": [{"distance": "NaN", "duration": 1}]},
                       {"code": "Ok", "routes": [{}]}, {"code": "Ok", "routes": "x"}):
            with patch(REQUEST, return_value=answer(forged)):
                with self.assertRaises(UserError):
                    self.route.with_user(self.manager).action_compute_distance()

    def test_distance(self):
        osrm = {"code": "Ok", "routes": [{"distance": 12345.0, "duration": 1800.0, "legs": [
            {"distance": 3000, "duration": 400}, {"distance": 3000, "duration": 400},
            {"distance": 3000, "duration": 400}, {"distance": 3345, "duration": 600}]}]}
        with patch(REQUEST, return_value=answer(osrm)) as call:
            self.route.with_user(self.manager).action_compute_distance()
        url = call.call_args.args[1]
        self.assertTrue(url.startswith("http://osrm:5000/route/v1/driving/-73.567300,45.501700;"))
        self.assertAlmostEqual(self.route.distance_km, 12.3, places=1)
        self.assertEqual(self.route.duration_minutes, 30)

    def test_plan_of_a_day_sets_times_and_respects_windows(self):
        day = self.make_day()
        osrm = {"code": "Ok", "routes": [{"distance": 9000.0, "duration": 1500.0, "legs": [
            {"distance": 3000, "duration": 300}, {"distance": 3000, "duration": 300},
            {"distance": 3000, "duration": 300}, {"distance": 0, "duration": 600}]}]}
        with patch(REQUEST, return_value=answer(osrm)):
            day.with_user(self.manager).action_compute_plan()
        alice, bob, carol = day.stop_ids.sorted("sequence")
        # Leaves 8:00 (12:00 UTC), 5 min to Alice, who opens at 9:00 (13:00 UTC).
        self.assertEqual(alice.planned_time, day.date_planned_start.replace(hour=13, minute=0))
        self.assertEqual(bob.planned_time, alice.planned_time + timedelta(minutes=10 + 5))
        self.assertAlmostEqual(day.distance_planned_km, 9.0)

    def test_optimize_reorders_and_keeps_unassigned(self):
        vroom = {"code": 0, "summary": {"distance": 8000, "duration": 1200},
                 "routes": [{"steps": [{"type": "start"}, {"type": "job", "id": 3},
                                       {"type": "job", "id": 1}, {"type": "end"}]}],
                 "unassigned": [{"id": 2}]}
        osrm = {"code": "Ok", "routes": [{"distance": 1, "duration": 1, "legs": []}]}
        with patch(REQUEST, side_effect=[answer(vroom), answer(osrm)]) as call:
            self.route.with_user(self.manager).action_optimize()
        payload = call.call_args_list[0].kwargs["json"]
        self.assertEqual(payload["jobs"][0]["time_windows"], [[9 * 3600, 11 * 3600]])
        self.assertEqual(payload["vehicles"][0]["time_window"][0], 8 * 3600)
        order = self.route.stop_ids.sorted("sequence").mapped("partner_id")
        self.assertEqual(order, self.carol | self.alice | self.bob)
        self.assertEqual(list(order), [self.carol, self.alice, self.bob])
        self.assertIn("Bob Garage", self.route.message_ids[0].body)
        distance_url = call.call_args_list[1].args[1]
        self.assertIn("-73.567300,45.501700;-73.550000,45.490000;-73.580000,45.520000;", distance_url,
                      "the distance is computed in the NEW order")

    def test_people_without_email_can_do_everything(self):
        # Odoo refuses a note outside sudo when its author has no email address: a route
        # manager or a worker may have none.
        self.manager.partner_id.email = False
        self.worker.partner_id.email = False
        vroom = {"code": 0, "summary": {"distance": 8000, "duration": 1200},
                 "routes": [{"steps": [{"type": "job", "id": 1}, {"type": "job", "id": 2}, {"type": "job", "id": 3}]}]}
        osrm = {"code": "Ok", "routes": [{"distance": 1, "duration": 1, "legs": []}]}
        with patch(REQUEST, side_effect=[answer(vroom), answer(osrm)]):
            self.route.with_user(self.manager).action_optimize()
        days = self.route._generate_days(today=MONDAY)
        days[1].with_user(self.manager).write({"vehicle_id": False})
        self.route.with_user(self.manager).write({"start_hour": 7.0, "mon": False, "tue": True})
        day = days[0] if days[0].exists() else self.make_day()
        self.start(day)
        day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("postponed", app_key="ne1")
        day.with_user(self.worker).app_report_refusal("ne2", reason="x")
        day.with_user(self.worker).app_finish()
        self.assertEqual(day.state, "done")

    def test_no_assignment_message_and_no_email_needed(self):
        self.manager.partner_id.email = False
        Route = self.env["bf.route"].with_user(self.manager).with_context(tracking_disable=False)
        route = Route.create({"name": "Assigned", "user_id": self.worker.id,
                              "responsible_id": self.manager.id, "recurrence": "weekly",
                              "date_start": MONDAY, "stop_ids": [(0, 0, {"partner_id": self.alice.id})]})
        days = route.with_context(tracking_disable=False)._generate_days(today=MONDAY)
        day = self.env["bf.route.day"].with_user(self.manager).with_context(
            tracking_disable=False, bf_route_copy_stops=True).create({"route_id": route.id,
                                                                      "date": MONDAY + timedelta(days=1)})
        all_days = days | day
        notices = self.env["mail.message"].search([
            "|", "&", ("model", "=", "bf.route"), ("res_id", "=", route.id),
            "&", ("model", "=", "bf.route.day"), ("res_id", "in", all_days.ids),
            ("message_type", "=", "user_notification")])
        self.assertFalse(notices, "no 'you have been assigned' message")
        self.assertNotIn(self.worker.partner_id, route.message_partner_ids)
        self.assertNotIn(self.worker.partner_id, all_days.message_partner_ids)

    def test_worker_cannot_optimize(self):
        day = self.make_day()
        with self.assertRaises(Exception):
            day.with_user(self.worker).action_optimize()
