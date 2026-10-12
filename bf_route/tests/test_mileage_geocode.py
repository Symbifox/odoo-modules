import ast
import requests
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import MONDAY, RouteCase
from .test_watch_engine import REQUEST, answer

OSRM = {"code": "Ok", "routes": [{"distance": 9000.0, "duration": 1500.0, "legs": [
    {"distance": 2000, "duration": 300}, {"distance": 2000, "duration": 300},
    {"distance": 2000, "duration": 300}, {"distance": 3000, "duration": 600}]}]}


def place(lat, lon, rank=30):
    return [{"lat": str(lat), "lon": str(lon), "place_rank": rank, "category": "place",
             "type": "house"}]


@tagged("post_install", "-at_install")
class TestMileageLog(RouteCase):

    def finished_day(self, start=1000, end=1085, marks=("done", "absent", "done")):
        day = self.make_day()
        self.start(day, odometer=start)
        for stop, mark in zip(day.stop_ids.sorted("sequence"), marks):
            if mark:
                stop.with_user(self.worker).app_mark(mark, app_key="k%s" % stop.id)
        day.with_user(self.worker).app_finish(odometer=end)
        return day

    def test_customers_visited_are_the_stops_driven_to_in_order(self):
        day = self.finished_day(marks=("done", "absent", None))
        self.assertEqual(day.visited, "Alice Café, Bob Garage", "an absent customer was driven to")
        day.stop_ids.filtered(lambda s: s.partner_id == self.bob).sequence = 1
        self.assertEqual(day.visited, "Bob Garage, Alice Café")

    def test_a_day_without_a_customer_reached_visits_nobody(self):
        day = self.finished_day(marks=("postponed", None, None))
        self.assertFalse(day.visited)

    def test_printed_log_groups_by_worker_and_vehicle_with_totals(self):
        day = self.finished_day()
        planned = self.make_day(day=MONDAY.replace(day=19))
        report = self.env["ir.actions.report"]
        html, _type = report.with_user(self.manager)._render_qweb_html(
            "bf_route.report_mileage_log", (day | planned).ids)
        html = html.decode()
        self.assertIn("Alice Café, Bob Garage, Carol Clinic", html)
        self.assertIn("ABC 123", html)
        self.assertIn("85.0", html)
        values = self.env["report.bf_route.report_mileage_log"]._get_report_values((day | planned).ids)
        self.assertEqual(values["docs"], day, "a day not finished is not in the log")
        self.assertEqual(len(values["groups"]), 1)
        self.assertAlmostEqual(values["groups"][0]["total"], 85.0)

    def test_worker_prints_their_own_log(self):
        day = self.finished_day()
        self.env.invalidate_all()
        html, _type = self.env["ir.actions.report"].with_user(self.worker)._render_qweb_html(
            "bf_route.report_mileage_log", day.ids)
        self.assertIn(b"Alice Caf", html)
        self.assertIn(b"ABC 123", html)

    def test_a_substitute_without_fleet_rights_prints_their_day(self):
        self.assertFalse(self.other.has_group("fleet.fleet_group_user"))
        day = self.make_day()
        day.user_id = self.other
        day.with_user(self.other).app_start(odometer=1000)
        day.stop_ids.sorted("sequence")[0].with_user(self.other).app_mark("done", app_key="s1")
        day.with_user(self.other).app_finish(odometer=1040)
        self.env.invalidate_all()
        html, _type = self.env["ir.actions.report"].with_user(self.other)._render_qweb_html(
            "bf_route.report_mileage_log", day.ids)
        self.assertIn(b"ABC 123", html)
        self.assertIn(b"Depot", html)

    def test_nobody_prints_someone_else_s_days(self):
        day = self.finished_day()
        self.env.invalidate_all()
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(self.other)._render_qweb_html(
                "bf_route.report_mileage_log", day.ids)

    def test_office_odometer_holds_to_the_phone_s_bounds(self):
        day = self.finished_day()
        with self.assertRaises(ValidationError):
            day.with_user(self.manager).write({"odometer_end": 1})
        with self.assertRaises(ValidationError):
            day.with_user(self.manager).write({"odometer_end": 1000 + 2001})
        with self.assertRaises(ValidationError):
            day.with_user(self.manager).write({"odometer_start": -5})
        day.with_user(self.manager).write({"odometer_end": 1100})
        self.assertAlmostEqual(day.distance_actual_km, 100.0)

    def test_mileage_log_action_shows_finished_days(self):
        day = self.finished_day()
        self.make_day(day=MONDAY.replace(day=19))
        action = self.env.ref("bf_route.action_bf_route_mileage")
        found = self.env["bf.route.day"].with_user(self.worker).search(ast.literal_eval(action.domain))
        self.assertEqual(found, day)


@tagged("post_install", "-at_install")
class TestGeocode(RouteCase):

    def setUp(self):
        super().setUp()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("bf_route.osrm_url", "http://osrm:5000")
        params.set_param("bf_route.nominatim_url", "http://nominatim:8080/")
        self.bob.write({"partner_latitude": 0, "partner_longitude": 0, "street": "55 rue Bob",
                        "city": "Laval", "zip": "H7A 1A1", "email": "bob@example.com",
                        "phone": "+1 450 555 0199",
                        "country_id": self.env.ref("base.ca").id})

    def test_a_route_locates_a_customer_without_coordinates(self):
        with patch(REQUEST, side_effect=[answer(place(45.6, -73.7)), answer(OSRM)]) as call:
            self.route.with_user(self.manager).action_compute_distance()
        self.assertAlmostEqual(self.bob.partner_latitude, 45.6)
        self.assertAlmostEqual(self.bob.partner_longitude, -73.7)
        method, url = call.call_args_list[0].args
        params = call.call_args_list[0].kwargs["params"]
        self.assertEqual((method, url), ("GET", "http://nominatim:8080/search"))
        self.assertEqual(params["street"], "55 rue Bob")
        self.assertEqual(params["city"], "Laval")
        self.assertEqual(params["countrycodes"], "ca")
        sent = repr(call.call_args_list[0])
        for private in ("Bob Garage", "bob@example.com", "555 0199"):
            self.assertNotIn(private, sent, "only the address leaves")
        self.assertAlmostEqual(self.route.distance_km, 9.0)

    def test_a_town_is_not_an_address(self):
        for rank in (16, 25):
            town = place(45.55, -73.75, rank=rank)
            with patch(REQUEST, side_effect=[answer(town), answer(town)]):
                with self.assertRaisesRegex(UserError, "No coordinates found.*Bob Garage"):
                    self.route.with_user(self.manager).action_compute_distance()
            self.assertFalse(self.bob.partner_latitude)

    def test_a_street_plans_but_is_not_written_on_the_contact(self):
        for rank in (26, 27):
            self.bob.write({"bf_route_street_latitude": 0, "bf_route_street_longitude": 0})
            with patch(REQUEST, side_effect=[answer(place(45.6, -73.7, rank=rank)), answer(OSRM)]) as call:
                self.route.with_user(self.manager).action_compute_distance()
            self.assertIn("-73.700000,45.600000", call.call_args_list[1].args[1], "the plan uses it")
            self.assertFalse(self.bob.partner_latitude, "the phone would navigate to the middle of the road")
            self.assertAlmostEqual(self.bob.bf_route_street_latitude, 45.6)
        with patch(REQUEST, side_effect=[answer(OSRM)]) as call:
            self.route.with_user(self.manager).action_compute_distance()
        self.assertEqual(call.call_count, 1, "the street point is not asked again")
        self.assertIn("-73.700000,45.600000", call.call_args.args[1])

    def test_out_of_time_says_so_and_points_to_the_contacts(self):
        with patch("odoo.addons.bf_route.models.bf_route_engine.GEOCODE_SECONDS", -1),                 patch(REQUEST) as call:
            with self.assertRaisesRegex(UserError, "Not located yet.*Bob Garage.*Locate on the map"):
                self.route.with_user(self.manager).action_compute_distance()
        call.assert_not_called()

    def test_an_interpolated_house_number_is_written(self):
        with patch(REQUEST, side_effect=[answer(place(45.6, -73.7, rank=28)), answer(OSRM)]):
            self.route.with_user(self.manager).action_compute_distance()
        self.assertAlmostEqual(self.bob.partner_latitude, 45.6)

    def test_a_contact_without_country_is_looked_up_in_the_company_s(self):
        self.company.country_id = self.env.ref("base.ca")
        self.bob.country_id = False
        with patch(REQUEST, side_effect=[answer(place(45.6, -73.7)), answer(OSRM)]) as call:
            self.route.with_user(self.manager).action_compute_distance()
        self.assertEqual(call.call_args_list[0].kwargs["params"]["countrycodes"], "ca")

    def test_a_contact_that_moves_loses_its_coordinates(self):
        self.alice.write({"street": "1 rue A"})
        self.assertAlmostEqual(self.alice.partner_latitude, 45.52, msg="same address, same place")
        self.alice.write({"street": " 1  Rue A ", "street2": "Suite 200"})
        self.assertAlmostEqual(self.alice.partner_latitude, 45.52, msg="spaces, case and the unit move nothing")
        self.bob.write({"bf_route_street_latitude": 45.6, "bf_route_street_longitude": -73.7})
        self.bob.write({"zip": "H7A1A1"})
        self.assertAlmostEqual(self.bob.bf_route_street_latitude, 45.6, msg="same postal code")
        self.bob.write({"zip": "H7B 2B2"})
        self.assertFalse(self.bob.bf_route_street_latitude, "the street point moves with the address")
        self.alice.write({"street": "99 autre rue"})
        self.assertFalse(self.alice.partner_latitude or self.alice.partner_longitude)
        self.alice.write({"city": "Laval", "partner_latitude": 45.6, "partner_longitude": -73.7})
        self.assertAlmostEqual(self.alice.partner_latitude, 45.6, msg="written with the address, kept")
        self.carol.write({"country_id": self.env.ref("base.fr").id})
        self.assertFalse(self.carol.partner_latitude)

    def test_tries_again_without_the_postal_code(self):
        with patch(REQUEST, side_effect=[answer([]), answer(place(45.6, -73.7)), answer(OSRM)]) as call:
            self.route.with_user(self.manager).action_compute_distance()
        self.assertNotIn("postalcode", call.call_args_list[1].kwargs["params"])
        self.assertEqual(call.call_args_list[0].kwargs["params"]["postalcode"], "H7A 1A1")
        self.assertAlmostEqual(self.bob.partner_latitude, 45.6)

    def test_forged_answers_write_nothing(self):
        for forged in ([{"lat": "NaN", "lon": "1", "place_rank": 30}], [{"lat": "95", "lon": "1",
                       "place_rank": 30}], {"error": "x"}, [{"place_rank": 30}], "x", [0]):
            with patch(REQUEST, side_effect=[answer(forged), answer(forged)]):
                with self.assertRaises(UserError):
                    self.route.with_user(self.manager).action_compute_distance()
            self.assertFalse(self.bob.partner_latitude)

    def test_geocoder_down_names_who_is_missing(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")) as call:
            with self.assertRaisesRegex(UserError, "Bob Garage"):
                self.route.with_user(self.manager).action_compute_distance()
        self.assertEqual(call.call_count, 1, "one try, not one per contact and per form")

    def test_without_geocoder_nothing_leaves(self):
        self.env["ir.config_parameter"].sudo().set_param("bf_route.nominatim_url", "")
        with patch(REQUEST) as call:
            with self.assertRaisesRegex(UserError, "no coordinates yet: Bob Garage"):
                self.route.with_user(self.manager).action_compute_distance()
        call.assert_not_called()

    def test_an_address_without_street_is_not_looked_up(self):
        self.bob.street = False
        with patch(REQUEST) as call:
            with self.assertRaises(UserError):
                self.route.with_user(self.manager).action_compute_distance()
        call.assert_not_called()

    def test_locate_from_the_contacts(self):
        with patch(REQUEST, side_effect=[answer(place(45.6, -73.7))]):
            action = (self.bob | self.alice).with_user(self.manager).action_bf_route_locate()
        self.assertEqual(action["params"]["type"], "success")
        self.assertIn("1 of 1", action["params"]["message"])
        self.assertAlmostEqual(self.bob.partner_latitude, 45.6)
        with patch(REQUEST) as call:
            action = self.bob.with_user(self.manager).action_bf_route_locate()
        call.assert_not_called()
        self.assertEqual(action["params"]["type"], "info")

    def test_locate_says_when_only_the_street_was_found(self):
        with patch(REQUEST, side_effect=[answer(place(45.6, -73.7, rank=26))]):
            action = self.bob.with_user(self.manager).action_bf_route_locate()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Street found", action["params"]["message"])
        self.assertFalse(self.bob.partner_latitude)
        self.assertAlmostEqual(self.bob.bf_route_street_latitude, 45.6)

    def test_locate_asks_again_for_a_street_point(self):
        self.bob.write({"bf_route_street_latitude": 45.6, "bf_route_street_longitude": -73.7})
        with patch(REQUEST, side_effect=[answer(place(45.61, -73.71, rank=30))]) as call:
            action = self.bob.with_user(self.manager).action_bf_route_locate()
        self.assertEqual(call.call_count, 1)
        self.assertAlmostEqual(self.bob.partner_latitude, 45.61)
        self.assertIn("1 of 1", action["params"]["message"])

    def test_locate_tells_not_tried_from_not_found(self):
        with patch(REQUEST, side_effect=requests.ConnectionError("down")):
            action = self.bob.with_user(self.manager).action_bf_route_locate()
        self.assertIn("Not tried yet", action["params"]["message"])
        self.assertNotIn("Not found", action["params"]["message"])

    def test_locate_names_the_ones_not_found(self):
        with patch(REQUEST, side_effect=[answer([]), answer([])]):
            action = self.bob.with_user(self.manager).action_bf_route_locate()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Bob Garage", action["params"]["message"])

    def test_locate_is_for_route_managers(self):
        for user in (self.worker, self.other):
            with patch(REQUEST) as call, self.assertRaises(AccessError):
                self.bob.with_user(user).action_bf_route_locate()
            call.assert_not_called()

    def test_locate_refuses_a_flood(self):
        many = self.env["res.partner"].create([{"name": "P%s" % i, "street": "1 rue", "city": "X"}
                                               for i in range(201)])
        with patch(REQUEST) as call, self.assertRaises(UserError):
            many.with_user(self.manager).action_bf_route_locate()
        call.assert_not_called()
