from odoo.tests import TransactionCase, tagged


@tagged("bf_helpdesk", "bf_helpdesk_waiting_state")
class TestTicketWaitingState(TransactionCase):
    """waiting_state Selection independent from stage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.Ticket = cls.env["helpdesk.ticket"]
        cls.alias = cls.env["mail.alias"].create({
            "alias_name": "waiting-state-test",
            "alias_model_id": cls.env.ref("helpdesk_mgmt.model_helpdesk_ticket").id,
        })
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Waiting State Team",
            "alias_id": cls.alias.id,
        })

    def _make_ticket(self):
        return self.Ticket.create({
            "name": "ws ticket",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })

    def test_default_no_waiting_state(self):
        ticket = self._make_ticket()
        self.assertFalse(ticket.waiting_state)

    def test_set_waiting_client(self):
        ticket = self._make_ticket()
        ticket.waiting_state = "client"
        self.assertEqual(ticket.waiting_state, "client")

    def test_set_waiting_external(self):
        ticket = self._make_ticket()
        ticket.waiting_state = "external"
        self.assertEqual(ticket.waiting_state, "external")

    def test_clear_waiting_state(self):
        ticket = self._make_ticket()
        ticket.waiting_state = "client"
        ticket.waiting_state = False
        self.assertFalse(ticket.waiting_state)

    def test_invalid_waiting_state_rejected(self):
        ticket = self._make_ticket()
        with self.assertRaises(ValueError):
            ticket.waiting_state = "bogus"
