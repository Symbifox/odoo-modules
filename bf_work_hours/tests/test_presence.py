from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

from ..controllers.main import WorkHoursController
from .common import WorkHoursCase, acting_as


@tagged("post_install", "-at_install")
class TestHeartbeat(HttpCase):
    """The browser's report, through the real route, in a real web session."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = new_test_user(cls.env, "wh_hb_alice", groups="base.group_user", tz="America/Montreal")
        cls.bob = new_test_user(cls.env, "wh_hb_bob", groups="base.group_user", tz="America/Montreal")

    def beat(self, inactivity_ms=1000):
        return self.make_jsonrpc_request("/bf_work_hours/heartbeat", {"inactivity_ms": inactivity_ms})

    def presence(self, user):
        return self.env["bf.work.signal"].sudo().search([("user_id", "=", user.id), ("source", "=", "presence")])

    def activate(self, user):
        self.env["bf.work.consent"].sudo().create({"user_id": user.id, "scope": "hours"})
        self.env.registry.clear_cache()

    def test_nothing_kept_without_activation(self):
        self.authenticate("wh_hb_alice", "wh_hb_alice")
        self.assertEqual(self.beat(), {"enabled": False})
        self.assertFalse(self.presence(self.alice))

    def test_stretches_are_kept_and_extended(self):
        self.activate(self.alice)
        self.authenticate("wh_hb_alice", "wh_hb_alice")
        now = fields.Datetime.now()
        with patch("odoo.fields.Datetime.now", return_value=now):
            self.assertEqual(self.beat(), {"enabled": True})
        with patch("odoo.fields.Datetime.now", return_value=now + timedelta(minutes=1)):
            self.beat()
        stretch = self.presence(self.alice)
        self.assertEqual(len(stretch), 1)
        self.assertEqual(stretch.date_stop - stretch.date_start, timedelta(minutes=1))
        with patch("odoo.fields.Datetime.now", return_value=now + timedelta(minutes=11)):
            self.beat()
        self.assertEqual(len(self.presence(self.alice)), 2)

    def test_idle_tab_is_not_activity(self):
        self.activate(self.alice)
        self.authenticate("wh_hb_alice", "wh_hb_alice")
        self.beat(10 * 60 * 1000)
        self.assertFalse(self.presence(self.alice))

    def test_a_session_reports_only_for_its_own_person(self):
        """Bob's session cannot write Alice's presence, by the route or by Odoo's own presence call."""
        self.activate(self.alice)
        self.authenticate("wh_hb_bob", "wh_hb_bob")
        self.beat()
        # Odoo's own presence call (public, any identity): it no longer feeds the working time.
        self.env["bus.presence"].with_user(self.bob)._update_presence(0, "user_id", self.alice.id)
        self.assertFalse(self.presence(self.alice))



@tagged("post_install", "-at_install")
class TestHeartbeatGuards(WorkHoursCase):
    """The route under an impersonation or an API key, with a simulated request.

    A real impersonation is validated on every request by bf_impersonate (journal,
    deadline): a hand-made session key would be dropped before reaching the route.
    """

    def call(self, user, path="/bf_work_hours/heartbeat", **session):
        with acting_as(user.id, path, **session) as fake:
            fake.env = self.env(user=user)
            with patch("odoo.addons.bf_work_hours.controllers.main.request", fake):
                return WorkHoursController.heartbeat.original_endpoint(WorkHoursController(), inactivity_ms=1000)

    def test_under_impersonation(self):
        self.activate(self.alice)
        self.assertEqual(self.call(self.alice, bf_impersonate={"from_uid": self.admin.id}), {"enabled": False})
        self.assertFalse(self.Signal.sudo().search([("user_id", "=", self.alice.id)]))

    def test_through_an_api_path(self):
        self.activate(self.alice)
        self.assertEqual(self.call(self.alice, path="/json/2/bf.work.signal"), {"enabled": False})

    def test_in_the_own_session(self):
        self.activate(self.alice)
        self.assertEqual(self.call(self.alice), {"enabled": True})
        self.assertTrue(self.Signal.sudo().search([("user_id", "=", self.alice.id)]))
