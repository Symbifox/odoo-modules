from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user
from odoo.tests import tagged

from .common import RouteCase

HERE = {"latitude": 45.52, "longitude": -73.58, "accuracy": 12}


@tagged("post_install", "-at_install")
class TestAccess(RouteCase):

    def test_other_worker_cannot_see_nor_mark(self):
        day = self.make_day()
        self.start(day)
        alice = day.stop_ids.sorted("sequence")[0]
        with self.assertRaises(AccessError):
            alice.with_user(self.other).app_mark("done")
        with self.assertRaises(AccessError):
            day.with_user(self.other).app_finish()
        self.assertFalse(self.env["bf.route.day"].with_user(self.other).search([("id", "=", day.id)]))

    def test_worker_cannot_write_directly(self):
        day = self.make_day()
        alice = day.stop_ids.sorted("sequence")[0]
        with self.assertRaises(AccessError):
            alice.with_user(self.worker).write({"state": "done"})
        with self.assertRaises(AccessError):
            day.with_user(self.worker).write({"odometer_end": 5})
        with self.assertRaises(AccessError):
            day.with_user(self.worker).action_cancel()
        with self.assertRaises(AccessError):
            self.route.with_user(self.worker).write({"name": "Mine now"})

    def test_a_contact_or_a_vehicle_of_another_company_is_refused(self):
        """The app_* methods read the stops' contacts, the depot and the truck in sudo: a
        manager of this company must not put those of another one on a route, and read them
        back through the phone's methods. A contact shared by all companies stays allowed."""
        other = self.env["res.company"].create({"name": "Company B"})
        secret = self.env["res.partner"].create({"name": "Secret of B", "company_id": other.id,
                                                 "phone": "+1 514 555 0199"})
        brand = self.env["fleet.vehicle.model.brand"].create({"name": "Brand B"})
        model = self.env["fleet.vehicle.model"].create({"name": "Van", "brand_id": brand.id})
        van = self.env["fleet.vehicle"].create({"model_id": model.id, "company_id": other.id})
        day = self.make_day()
        stop = day.stop_ids.sorted("sequence")[0]
        Stop = self.env["bf.route.day.stop"].with_user(self.manager)
        with self.assertRaises(UserError):
            Stop.create({"day_id": day.id, "partner_id": secret.id})
        with self.assertRaises(UserError):
            stop.with_user(self.manager).write({"partner_id": secret.id})
        route = self.route.with_user(self.manager)
        with self.assertRaises(UserError):
            route.write({"stop_ids": [(0, 0, {"partner_id": secret.id})]})
        with self.assertRaises(UserError):
            route.stop_ids[0].write({"partner_id": secret.id})
        for field in ("start_partner_id", "end_partner_id"):
            with self.assertRaises(UserError, msg=field):
                route.write({field: secret.id})
        with self.assertRaises(UserError):
            route.write({"vehicle_id": van.id})
        with self.assertRaises(UserError):
            day.with_user(self.manager).write({"vehicle_id": van.id})
        # Not in sudo either (the satellites, a postponed stop).
        with self.assertRaises(UserError):
            Stop.sudo().create({"day_id": day.id, "partner_id": secret.id})
        self.assertNotIn(secret, day.stop_ids.partner_id | self.route.stop_ids.partner_id)
        # A contact without a company, and one of this company, still go on a stop.
        mine = self.env["res.partner"].create({"name": "Mine", "company_id": self.company.id})
        added = Stop.create({"day_id": day.id, "partner_id": mine.id})
        added.write({"partner_id": self.alice.id})
        self.assertEqual(added.partner_id, self.alice)

    def test_a_route_that_cannot_make_its_days_does_not_stop_the_others(self):
        """A stop saved with another company's contact before the check existed: its route
        makes no day, the other routes still get theirs from the watcher."""
        other = self.env["res.company"].create({"name": "Company B"})
        secret = self.env["res.partner"].create({"name": "Secret of B", "company_id": other.id})
        broken = self.route.copy({"name": "Broken"})
        (self.route | broken).with_context(bf_route_no_refresh=True).write({
            "date_start": fields.Date.today() - timedelta(days=7),
            "mon": True, "tue": True, "wed": True, "thu": True, "fri": True, "sat": True, "sun": True})
        self.env.cr.execute("UPDATE bf_route_stop SET partner_id = %s WHERE route_id = %s",
                            (secret.id, broken.id))
        self.env.invalidate_all()
        self.assertFalse((self.route | broken).day_ids)
        with self.assertLogs("odoo.addons.bf_route.models.bf_route", level="ERROR") as logged:
            self.env["bf.route"]._cron_watch()
        self.assertTrue(self.route.day_ids, "the sound route got its days")
        self.assertFalse(broken.day_ids)
        self.assertTrue(any("route %s could not" % broken.id in line for line in logged.output))

    def test_worker_cannot_run_manager_actions(self):
        for method in ("action_generate_days", "action_compute_distance", "action_optimize",
                       "action_plan_day"):
            with self.assertRaises(AccessError, msg=method):
                getattr(self.route.with_user(self.worker), method)()

    def test_engine_is_not_reachable_from_outside(self):
        Engine = self.env["bf.route.engine"]
        self.assertFalse(hasattr(Engine, "route") or hasattr(Engine, "optimize"),
                         "only private methods: not callable over RPC")

    def test_only_workers_acknowledge_the_notice(self):
        self.company.bf_route_position_mode = "stops"
        stranger = new_test_user(self.env, login="route_stranger", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.env["bf.route.notice.ack"].with_user(stranger).app_acknowledge(
                self.company.bf_route_notice_version)

    def test_a_one_day_replacement_does_not_read_the_route(self):
        day = self.make_day()
        day.user_id = self.other
        self.assertFalse(self.env["bf.route"].with_user(self.other).search([("id", "=", self.route.id)]))
        self.assertFalse(self.env["bf.route.stop"].with_user(self.other).search(
            [("route_id", "=", self.route.id)]))
        data = self.env["bf.route.day"].with_user(self.other).browse(day.id)._app_data()
        self.assertEqual(data["name"], "North shore", "the phone still names the route")

    def test_worker_cannot_read_positions(self):
        day = self.make_day()
        alice = day.stop_ids.sorted("sequence")[0]
        with self.assertRaises(AccessError):
            alice.with_user(self.worker).read(["latitude"])


@tagged("post_install", "-at_install")
class TestPosition(RouteCase):

    def mark_alice(self, position=HERE):
        day = self.make_day()
        self.start(day)
        alice = day.stop_ids.sorted("sequence")[0]
        alice.with_user(self.worker).app_mark("done", position=position)
        return alice

    def test_off_reads_nothing(self):
        Ack = self.env["bf.route.notice.ack"].with_user(self.worker)
        with self.assertRaises(UserError):
            Ack.app_acknowledge(self.company.bf_route_notice_version)
        alice = self.mark_alice()
        self.assertFalse(alice.latitude)

    def test_on_without_notice_reads_nothing(self):
        self.company.bf_route_position_mode = "stops"
        status = self.env["bf.route.notice.ack"].with_user(self.worker)._status(self.company)
        self.assertTrue(status["must_read"])
        self.assertIn("once", status["text"])
        alice = self.mark_alice()
        self.assertFalse(alice.latitude)

    def test_on_with_notice_keeps_the_position_then_erases_it(self):
        self.company.bf_route_position_mode = "stops"
        Ack = self.env["bf.route.notice.ack"].with_user(self.worker)
        with self.assertRaises(UserError):
            Ack.app_acknowledge(self.company.bf_route_notice_version + 1)
        status = Ack.app_acknowledge(self.company.bf_route_notice_version)
        self.assertTrue(status["may_read_position"])
        ack = self.env["bf.route.notice.ack"].search([("user_id", "=", self.worker.id)])
        self.assertIn("once", ack.notice, "the text read is kept as evidence")
        alice = self.mark_alice()
        self.assertAlmostEqual(alice.latitude, 45.52)
        self.assertAlmostEqual(alice.accuracy, 12)
        alice.done_at = fields.Datetime.now() - timedelta(days=91)
        self.env["bf.route.day.stop"]._purge_positions()
        self.assertFalse(alice.latitude)
        self.assertTrue(alice.position_erased)

    def test_new_notice_must_be_read_again(self):
        self.company.bf_route_position_mode = "stops"
        Ack = self.env["bf.route.notice.ack"].with_user(self.worker)
        Ack.app_acknowledge(self.company.bf_route_notice_version)
        self.company.bf_route_notice = "<p>New wording</p>"
        self.assertTrue(Ack._status(self.company)["must_read"])
        alice = self.mark_alice()
        self.assertFalse(alice.latitude)

    def test_the_notice_of_a_company_not_mine_is_not_acknowledged(self):
        self.company.bf_route_position_mode = "stops"
        stranger = self.env["res.company"].create({"name": "Not the worker's"})
        status = self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version, stranger.id)
        self.assertEqual(status["company_id"], self.company.id)

    def test_longer_retention_must_be_read_again(self):
        self.company.bf_route_position_mode = "stops"
        Ack = self.env["bf.route.notice.ack"].with_user(self.worker)
        Ack.app_acknowledge(self.company.bf_route_notice_version)
        self.company.bf_route_position_days = 3650
        self.assertTrue(Ack._status(self.company)["must_read"])

    def test_positions_of_an_archived_company_are_erased_too(self):
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version)
        alice = self.mark_alice().sudo()
        alice.done_at = fields.Datetime.now() - timedelta(days=91)
        closed = self.env["res.company"].create({"name": "Closed company"})
        # The truck goes with the route (a vehicle of the old company would be refused).
        self.truck.company_id = False
        self.route.sudo().company_id = closed
        self.assertEqual(alice.company_id, closed)
        self.assertTrue(alice.latitude)
        closed.active = False
        self.env["bf.route"]._cron_watch()
        self.assertFalse(alice.latitude, "the position of a stop of an archived company is erased")

    def test_the_phone_learns_when_positions_are_turned_off(self):
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version)
        day = self.make_day()
        self.start(day)
        self.company.bf_route_position_mode = "off"
        data = day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("done", position=HERE)
        self.assertFalse(data["_notice"]["may_read_position"])

    def test_the_notice_says_one_day_not_one_days(self):
        Ack = self.env["bf.route.notice.ack"]
        self.company.bf_route_position_days = 1
        self.assertIn("1 day, then", Ack._default_notice(self.company))
        self.company.bf_route_position_days = 30
        self.assertIn("30 days, then", Ack._default_notice(self.company))

    def test_absurd_position_is_ignored(self):
        self.company.bf_route_position_mode = "stops"
        self.env["bf.route.notice.ack"].with_user(self.worker).app_acknowledge(
            self.company.bf_route_notice_version)
        alice = self.mark_alice(position={"latitude": 500, "longitude": "x"})
        self.assertFalse(alice.latitude)
        self.assertEqual(alice.state, "done", "a bad position never blocks the mark")
