from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("bf_helpdesk", "bf_helpdesk_presence")
class TestPresence(TransactionCase):
    """présence en direct et actions à raccourci."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        groups = "base.group_user,helpdesk_mgmt.group_helpdesk_user_team"
        cls.alice = new_test_user(cls.env, login="pr-alice", name="Alice", groups=groups)
        cls.bruno = new_test_user(cls.env, login="pr-bruno", name="Bruno", groups=groups)
        cls.outsider = new_test_user(cls.env, login="pr-dehors", name="Dehors", groups=groups)
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Présence",
            "user_ids": [(4, cls.alice.id), (4, cls.bruno.id)],
        })
        cls.ticket = cls.env["helpdesk.ticket"].create({
            "name": "billet présence", "description": "<p>x</p>",
            "team_id": cls.team.id,
        })

    def test_ping_lists_other_agents_only(self):
        self.ticket.with_user(self.alice).bf_presence_ping()
        res = self.ticket.with_user(self.bruno).bf_presence_ping()
        self.assertEqual([o["name"] for o in res["others"]], ["Alice"])
        res = self.ticket.with_user(self.alice).bf_presence_ping()
        self.assertEqual([o["name"] for o in res["others"]], ["Bruno"])
        self.env.cr.flush()

    def test_presence_expires(self):
        now = fields.Datetime.now()
        with freeze_time(now):
            self.ticket.with_user(self.alice).bf_presence_ping()
        with freeze_time(now + timedelta(seconds=120)):
            res = self.ticket.with_user(self.bruno).bf_presence_ping()
        self.assertEqual(res["others"], [])

    def test_leave_removes_presence(self):
        self.ticket.with_user(self.alice).bf_presence_ping()
        self.ticket.with_user(self.alice).bf_presence_leave()
        res = self.ticket.with_user(self.bruno).bf_presence_ping()
        self.assertEqual(res["others"], [])

    def test_ping_requires_read_access(self):
        with self.assertRaises(AccessError):
            self.ticket.with_user(self.outsider).bf_presence_ping()

    def test_agent_cannot_read_presence_table_directly(self):
        self.ticket.with_user(self.alice).bf_presence_ping()
        with self.assertRaises(AccessError):
            self.env["helpdesk.ticket.presence"].with_user(self.bruno).search([])

    def test_toggle_waiting(self):
        ticket = self.ticket.with_user(self.bruno)
        ticket.action_bf_toggle_waiting_client()
        self.assertEqual(self.ticket.waiting_state, "client")
        self.assertTrue(self.ticket.sla_paused_since)
        ticket.action_bf_toggle_waiting_client()
        self.assertFalse(self.ticket.waiting_state)
        self.assertFalse(self.ticket.sla_paused_since)
        # Le suivi d'historique ne lève qu'au vidage : le forcer ici.
        self.env.cr.flush()
