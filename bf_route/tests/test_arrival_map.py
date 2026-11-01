"""Arrival at a stop ("I'm here"), the time on site, and the map of a day (18.0.1.2.0)."""
import xmlrpc.client
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import MONDAY, RouteCase
from .test_watch_engine import REQUEST, answer


def iso(value):
    return fields.Datetime.to_string(value).replace(" ", "T") + "Z"


@tagged("post_install", "-at_install")
class TestArrival(RouteCase):

    def setUp(self):
        super().setUp()
        self.day = self.make_day()
        self.stop = self.day.stop_ids.sorted("sequence")[0]

    def arrive(self, stop=None, user=None, **kwargs):
        return (stop or self.stop).with_user(user or self.worker).app_arrive(**kwargs)

    def test_arrival_needs_a_started_day(self):
        with self.assertRaisesRegex(UserError, "Start the day"):
            self.arrive()
        self.assertFalse(self.stop.arrived_at)

    def test_arrival_then_departure_gives_the_time_on_site(self):
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=1)))
        data = self.arrive(client_time=iso(now - timedelta(minutes=25)), app_key="a-1")
        self.assertTrue(data["arrived_at"], "the phone gets the arrival time back")
        self.stop.with_user(self.worker).app_mark("done", client_time=iso(now), app_key="m-1")
        self.assertEqual(self.stop.on_site_minutes, 25)
        self.assertTrue(self.stop.arrived_clock)

    def test_arrival_sent_twice_keeps_the_first_time(self):
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=1)))
        self.arrive(client_time=iso(now - timedelta(minutes=10)), app_key="k-1")
        first = self.stop.arrived_at
        self.arrive(client_time=iso(now), app_key="k-1")
        self.arrive(client_time=iso(now), app_key="k-2")
        self.assertEqual(self.stop.arrived_at, first)

    def test_arrival_on_a_marked_stop_is_refused(self):
        self.start(self.day)
        self.stop.with_user(self.worker).app_mark("absent", app_key="m-2")
        with self.assertRaisesRegex(UserError, "already marked"):
            self.arrive()

    def test_arrival_kept_after_the_mark_of_the_stop(self):
        """The phone's queue may resend the arrival after the mark went through."""
        self.start(self.day)
        self.arrive(app_key="a-3")
        self.stop.with_user(self.worker).app_mark("done", app_key="m-3")
        data = self.arrive(app_key="a-3")
        self.assertEqual(data["state"], "done")

    def test_departure_never_before_the_arrival(self):
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=1)))
        self.arrive(client_time=iso(now - timedelta(minutes=5)))
        self.stop.with_user(self.worker).app_mark("done", client_time=iso(now - timedelta(minutes=30)))
        self.assertEqual(self.stop.done_at, self.stop.arrived_at)
        self.assertEqual(self.stop.on_site_minutes, 0)

    def test_marking_without_arrival_still_works(self):
        self.start(self.day)
        self.stop.with_user(self.worker).app_mark("done")
        self.assertEqual(self.stop.state, "done")
        self.assertFalse(self.stop.arrived_at)
        self.assertEqual(self.stop.on_site_minutes, 0)

    def test_arrival_time_from_the_future_or_before_the_start_is_not_trusted(self):
        self.start(self.day)
        now = fields.Datetime.now()
        self.arrive(client_time=iso(now + timedelta(hours=3)))
        self.assertLessEqual(self.stop.arrived_at, fields.Datetime.now())
        other = self.day.stop_ids.sorted("sequence")[1]
        self.arrive(stop=other, client_time=iso(self.day.date_started - timedelta(hours=1)))
        self.assertGreaterEqual(other.arrived_at, self.day.date_started)

    def test_arrival_is_never_in_the_future(self):
        """The phone's clock a few minutes ahead (within the tolerance of the marks): the
        arrival is now, and the departure that follows is not pushed into the future."""
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=1)))
        self.arrive(client_time=iso(now + timedelta(minutes=4)))
        self.assertLessEqual(self.stop.arrived_at, fields.Datetime.now())
        self.stop.with_user(self.worker).app_mark("done", client_time=iso(now))
        self.assertLessEqual(self.stop.done_at, fields.Datetime.now())

    def test_a_day_closed_by_the_office_says_so(self):
        """Not "Start the day": the day is over. Said for the arrival and for the mark."""
        self.start(self.day)
        self.arrive(app_key="a-closed")
        self.day.with_user(self.manager).action_finish()
        stop = self.day.stop_ids.sorted("sequence")[1]
        for call in (lambda: self.arrive(stop=stop),
                     lambda: stop.with_user(self.worker).app_mark("done", app_key="m-closed")):
            with self.assertRaisesRegex(UserError, "This day is finished"):
                call()
        later = self.make_day(MONDAY + timedelta(days=7))
        later.with_user(self.manager).action_cancel()
        stop = later.stop_ids[:1]
        for call in (lambda: self.arrive(stop=stop),
                     lambda: stop.with_user(self.worker).app_mark("done", app_key="m-cancel")):
            with self.assertRaisesRegex(UserError, "This day was cancelled"):
                call()
        planned = self.make_day(MONDAY + timedelta(days=14)).stop_ids[:1]
        with self.assertRaisesRegex(UserError, "Start the day before marking"):
            planned.with_user(self.worker).app_mark("done")

    def test_time_on_site_adds_up_in_the_visits(self):
        """Stored, so the Visits list sums it and groups by it."""
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=2)))
        first, second = self.day.stop_ids.sorted("sequence")[:2]
        for stop, minutes in ((first, 25), (second, 10)):
            stop.with_user(self.worker).app_arrive(client_time=iso(now - timedelta(minutes=minutes + 30)))
            stop.with_user(self.worker).app_mark("done", client_time=iso(now - timedelta(minutes=30)))
        Stop = self.env["bf.route.day.stop"].with_user(self.manager)
        [(total,)] = Stop._read_group([("day_id", "=", self.day.id)], [], ["on_site_minutes:sum"])
        self.assertEqual(total, 35)
        arch = self.env.ref("bf_route.view_bf_route_day_stop_list").arch
        self.assertRegex(arch, r'name="on_site_minutes"[^>]*sum="Total"')

    def test_a_stop_put_back_to_do_has_no_time_on_site(self):
        """The office puts a marked stop back to "to do": its old mark closes nothing, the
        Visits list does not add it up. The arrival stays (the worker was there)."""
        now = fields.Datetime.now()
        self.start(self.day, client_time=iso(now - timedelta(hours=2)))
        self.arrive(client_time=iso(now - timedelta(minutes=50)))
        self.stop.with_user(self.worker).app_mark("postponed", client_time=iso(now - timedelta(minutes=20)))
        self.assertEqual(self.stop.on_site_minutes, 30)
        self.stop.with_user(self.manager).write({"state": "todo"})
        self.assertEqual(self.stop.on_site_minutes, 0)
        self.assertTrue(self.stop.arrived_at)
        Stop = self.env["bf.route.day.stop"].with_user(self.manager)
        [(total,)] = Stop._read_group([("day_id", "=", self.day.id)], [], ["on_site_minutes:sum"])
        self.assertEqual(total, 0)

    def test_the_address_for_directions_has_the_postal_code_and_the_province(self):
        """Sent to the maps app when the stop has no coordinates: "1 rue A, Montréal" alone
        exists in many towns."""
        self.alice.write({"zip": "H2X 1Y4", "state_id": self.env.ref("base.state_ca_qc").id})
        data = self.day.with_user(self.worker)._app_data()["stops"][0]
        self.assertEqual(data["address"], "1 rue A, Montréal, QC H2X 1Y4")
        self.alice.write({"zip": False, "state_id": False})
        data = self.day.with_user(self.worker)._app_data()["stops"][0]
        self.assertEqual(data["address"], "1 rue A, Montréal")

    def test_refused_arrival_is_reported_in_words(self):
        """The office reads "Arrival" (or the mark's label), not the phone's codes."""
        self.start(self.day)
        Day = self.day.with_user(self.worker)
        Day.app_report_refusal("r-arrive", stop_id=self.stop.id, reason="closed", state="arrived")
        self.assertIn("It carried: Arrival", self.day.message_ids[0].body)
        self.assertNotIn("arrived", self.day.message_ids[0].body)
        Day.app_report_refusal("r-absent", stop_id=self.stop.id, reason="closed", state="absent")
        self.assertIn("It carried: Customer absent", self.day.message_ids[0].body)

    def test_another_worker_cannot_arrive(self):
        self.start(self.day)
        with self.assertRaises(AccessError):
            self.arrive(user=self.other)
        self.assertFalse(self.stop.arrived_at)

    def test_worker_cannot_write_the_arrival_directly(self):
        self.start(self.day)
        with self.assertRaises(AccessError):
            self.stop.with_user(self.worker).write({"arrived_at": fields.Datetime.now()})

    def test_arrival_reads_no_position(self):
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version, self.company.id)
        self.start(self.day)
        with self.assertRaises(TypeError):
            self.stop.with_user(self.worker).app_arrive(position={"latitude": 45.0, "longitude": -73.0})
        self.assertFalse(self.stop.sudo().latitude)

    def test_postponed_copy_does_not_carry_the_arrival(self):
        nxt = self.make_day(self.day.date + timedelta(days=7))
        self.start(self.day)
        self.arrive()
        self.stop.with_user(self.worker).app_mark("postponed")
        moved = nxt.stop_ids.filtered(lambda s: s.partner_id == self.stop.partner_id)
        self.assertTrue(moved)
        self.assertFalse(moved.arrived_at)


@tagged("post_install", "-at_install")
class TestMap(RouteCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("bf_route.osrm_url", "http://osrm:5000")
        self.day = self.make_day()

    def map(self, user=None):
        return self.day.with_user(user or self.worker).app_map()

    def test_points_in_order_with_the_depot(self):
        osrm = {"code": "Ok", "routes": [{"geometry": {"coordinates": [
            [-73.5673, 45.5017], [-73.58, 45.52], [-73.60, 45.53]]}}]}
        with patch(REQUEST, return_value=answer(osrm)) as call:
            data = self.map()
        self.assertEqual([p["kind"] for p in data["points"]], ["depot", "stop", "stop", "stop"])
        self.assertEqual([p["label"] for p in data["points"][1:]], ["Alice Café", "Bob Garage", "Carol Clinic"])
        self.assertEqual(data["line"][0], [45.5017, -73.5673], "the road comes back as [lat, lon]")
        self.assertIn("overview", call.call_args.kwargs["params"])
        # The depot closes the loop: the road comes back to it.
        coords = call.call_args.args[1].rsplit("/", 1)[1].split(";")
        self.assertEqual(coords[0], coords[-1])
        self.assertEqual(len(coords), 5)

    def test_never_the_worker_s_positions(self):
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version, self.company.id)
        self.start(self.day)
        stop = self.day.stop_ids.sorted("sequence")[0]
        stop.with_user(self.worker).app_mark("done", position={"latitude": 46.9, "longitude": -71.1,
                                                               "accuracy": 5})
        self.assertAlmostEqual(stop.sudo().latitude, 46.9)
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            data = self.map(user=self.manager)
        flat = repr(data)
        self.assertNotIn("46.9", flat)
        self.assertNotIn("-71.1", flat)
        self.assertEqual(data["points"][1]["lat"], 45.52)

    def test_engine_down_draws_straight_lines(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            data = self.map()
        self.assertFalse(data["line"])
        self.assertEqual(len(data["points"]), 4)

    def test_no_engine_no_call(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_route.osrm_url", "")
        with patch(REQUEST) as call:
            data = self.map()
        call.assert_not_called()
        self.assertFalse(data["line"])

    def test_forged_geometry_is_dropped(self):
        for forged in ({"code": "Ok", "routes": [{"geometry": {"coordinates": [[500, 45], [1, 1]]}}]},
                       {"code": "Ok", "routes": [{"geometry": {"coordinates": "x"}}]},
                       {"code": "Ok", "routes": [{}]}):
            with patch(REQUEST, return_value=answer(forged)):
                self.assertFalse(self.map()["line"])

    def test_road_of_fewer_than_two_points_falls_back_to_straight_lines(self):
        """An empty road, or one of a single place, is no road: False, so the phone joins the
        stops by straight lines and frames them ([] would stop both)."""
        for coordinates in ([], [[-73.58, 45.52]], [[-73.58, 45.52], [-73.58, 45.52]]):
            osrm = {"code": "Ok", "routes": [{"geometry": {"coordinates": coordinates}}]}
            with patch(REQUEST, return_value=answer(osrm)):
                self.assertIs(self.map()["line"], False, coordinates)

    def test_long_road_is_thinned(self):
        coords = [[-73.5 - i * 1e-5, 45.5 + i * 1e-5] for i in range(10000)]
        with patch(REQUEST, return_value=answer({"code": "Ok", "routes": [{"geometry": {"coordinates": coords}}]})):
            line = self.map()["line"]
        self.assertLessEqual(len(line), 4001)
        self.assertEqual(line[-1], [round(coords[-1][1], 6), round(coords[-1][0], 6)])

    def test_place_without_coordinates_is_listed_without_any(self):
        self.bob.write({"partner_latitude": 0, "partner_longitude": 0})
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            data = self.map()
        bob = next(p for p in data["points"] if p["label"] == "Bob Garage")
        self.assertIs(bob["lat"], False)
        self.assertIs(bob["lon"], False)
        # What the phone gets over XML-RPC as well: None cannot be sent.
        xmlrpc.client.dumps((data,), methodresponse=True)

    def test_street_point_when_the_number_was_not_found(self):
        self.bob.write({"partner_latitude": 0, "partner_longitude": 0,
                        "bf_route_street_latitude": 45.6, "bf_route_street_longitude": -73.7})
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            data = self.map()
        bob = next(p for p in data["points"] if p["label"] == "Bob Garage")
        self.assertEqual((bob["lat"], bob["lon"]), (45.6, -73.7))

    def test_another_worker_cannot_see_the_map(self):
        with self.assertRaises(AccessError):
            self.map(user=self.other)

    def test_road_waits_three_seconds_at_most(self):
        """OSRM unreachable: the map draws straight lines after a short wait, not 20 s."""
        with patch(REQUEST, side_effect=requests.ConnectionError("down")) as call:
            self.assertFalse(self.map()["line"])
        self.assertLessEqual(call.call_args.kwargs["timeout"], 3)

    def test_answer_that_is_not_an_object(self):
        """A list or a string from OSRM (or VROOM): a UserError, so the map falls back."""
        Engine = self.env["bf.route.engine"]
        self.env["ir.config_parameter"].sudo().set_param("bf_route.vroom_url", "http://vroom:3000")
        for forged in ([1, 2], "x", None):
            with patch(REQUEST, return_value=answer(forged)):
                self.assertFalse(self.map()["line"], forged)
                with self.assertRaises(UserError):
                    Engine._route([[-73.5, 45.5], [-73.6, 45.6]])
                with self.assertRaises(UserError):
                    Engine._route_line([[-73.5, 45.5], [-73.6, 45.6]])
                with self.assertRaises(UserError):
                    Engine._optimize(self.depot, self.depot, self.day.stop_ids)

    def test_manager_of_another_company_is_refused(self):
        """A manager sees every day of THEIR companies: not a day of another one, though the
        methods go on in sudo (the places of its customers, a refusal posted on it)."""
        other = self.env["res.company"].create({"name": "Another company"})
        stranger = new_test_user(self.env, login="route_stranger", company_id=other.id,
                                 company_ids=[(6, 0, other.ids)],
                                 groups="base.group_user,bf_route.group_route_user")
        route = self.env["bf.route"].sudo().create({
            "name": "Elsewhere", "company_id": other.id, "user_id": stranger.id,
            "start_partner_id": self.depot.id, "recurrence": "weekly", "date_start": MONDAY,
            "stop_ids": [(0, 0, {"partner_id": self.alice.id})]})
        day = self.env["bf.route.day"].sudo().with_context(bf_route_copy_stops=True).create(
            {"route_id": route.id, "date": MONDAY})
        day.with_user(stranger).app_start()
        stop = day.stop_ids
        as_manager = day.with_user(self.manager)
        with self.assertRaises(AccessError):
            as_manager.app_map()
        with self.assertRaises(AccessError):
            as_manager.app_report_refusal("x-1", stop_id=stop.id, reason="probe")
        with self.assertRaises(AccessError):
            stop.with_user(self.manager).app_arrive()
        with self.assertRaises(AccessError):
            stop.with_user(self.manager).app_mark("done")
        self.assertFalse(day.refusal_keys)
        self.assertEqual(stop.state, "todo")
        self.assertFalse(stop.arrived_at)
        # Its own worker, a manager of that company, and the system still get through.
        boss = new_test_user(self.env, login="route_boss", company_id=other.id,
                             company_ids=[(6, 0, other.ids)],
                             groups="base.group_user,bf_route.group_route_manager")
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            expected = day.sudo().app_map()["points"]
            self.assertTrue(expected)
            self.assertEqual(day.with_user(stranger).app_map()["points"], expected)
            self.assertEqual(day.with_user(boss).app_map()["points"], expected)
        stop.with_user(boss).app_arrive()
        self.assertTrue(stop.arrived_at)

    def test_default_tiles_are_openstreetmap(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            tiles = self.map()["tiles"]
        self.assertEqual(tiles["url"], "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.assertIn("OpenStreetMap", tiles["attribution"])

    def test_tile_server_of_the_administrator(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("bf_route.tile_url", "https://tiles.example.org/{z}/{x}/{y}.png")
        params.set_param("bf_route.tile_attribution", "<b>Tiles</b> by us")
        tiles = self.env["bf.route.engine"]._tiles()
        self.assertEqual(tiles["url"], "https://tiles.example.org/{z}/{x}/{y}.png")
        self.assertEqual(tiles["attribution"], "<b>Tiles</b> by us", "plain text: the phone escapes it")
        self.assertFalse(tiles["attribution_url"])
        # Leaflet's sub-domain and retina placeholders are kept.
        params.set_param("bf_route.tile_url", "https://{s}.tiles.example.org/{z}/{x}/{y}{r}.png")
        self.assertEqual(self.env["bf.route.engine"]._tiles()["url"],
                         "https://{s}.tiles.example.org/{z}/{x}/{y}{r}.png")

    def test_unsafe_tile_addresses_fall_back(self):
        params = self.env["ir.config_parameter"].sudo()
        for bad in ("http://tiles.example.org/{z}/{x}/{y}.png", "javascript:alert(1)//{z}{x}{y}",
                    "https://tiles.example.org/tile.png", 'https://x.org/{z}/{x}/{y}.png" onerror="x',
                    "https://x.org/{z}/{x}/{y}\\.png",
                    # credentials in the address, and braces that are not Leaflet's placeholders
                    "https://user:pass@tiles.example.org/{z}/{x}/{y}.png",
                    "https://tiles.example.org@evil.example/{z}/{x}/{y}.png",
                    "https://x.org/{z}/{x}/{y}/{foo}.png", "https://x.org/{z}/{x}/{y}.png?k={",
                    "https://x.org/{z}/{x}/{y}.png}"):
            params.set_param("bf_route.tile_url", bad)
            self.assertEqual(self.env["bf.route.engine"]._tiles()["url"],
                             "https://tile.openstreetmap.org/{z}/{x}/{y}.png", bad)
