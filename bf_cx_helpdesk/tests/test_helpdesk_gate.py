"""Automatic detractor ticket: the switch and the closed-loop contract.

Unlike the other bridges this one creates no outbound mail - it opens a
ticket. The risk it carries is different: a ticket storm on the Plaintes
team, and a closed loop that stops working because the extension raised
instead of the core finishing its job.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestAutoTicketGate(CxBridgeCase):

    def _detractor(self):
        return self.env["bf.cx.feedback"].create(
            {
                "partner_id": self.cx_partner.id,
                "kind": "nps",
                "score": 2,
                "score_max": 10,
                "source": "manual",
                "comment": "Délais trop longs.",
            }
        )

    def test_gate_off_creates_no_ticket(self):
        self.set_gate("bf_cx.auto_ticket", False)
        feedback = self._detractor()
        feedback._run_closed_loop()
        self.assertFalse(feedback.helpdesk_ticket_id)

    def test_gate_on_creates_one_ticket(self):
        self.set_gate("bf_cx.auto_ticket", True)
        feedback = self._detractor()
        feedback._run_closed_loop()
        ticket = feedback.helpdesk_ticket_id
        self.assertTrue(ticket)
        self.assertEqual(ticket.partner_id, self.cx_partner)
        self.assertIn("Délais trop longs", ticket.description)
        # The loop can run again (a corrected rating re-triggers it):
        # it must not open a second ticket.
        feedback._run_closed_loop()
        self.assertEqual(feedback.helpdesk_ticket_id, ticket)

    def test_promoter_gets_no_ticket(self):
        self.set_gate("bf_cx.auto_ticket", True)
        feedback = self.env["bf.cx.feedback"].create(
            {
                "partner_id": self.cx_partner.id,
                "kind": "nps",
                "score": 10,
                "score_max": 10,
                "source": "manual",
            }
        )
        feedback._run_closed_loop()
        self.assertFalse(feedback.helpdesk_ticket_id)

    def test_a_failing_ticket_never_breaks_the_closed_loop(self):
        """The public /rate route runs this: it must not raise there."""
        from unittest.mock import patch

        self.set_gate("bf_cx.auto_ticket", True)
        feedback = self._detractor()
        with patch.object(
            type(feedback),
            "_create_followup_ticket",
            side_effect=RuntimeError("boom"),
            autospec=True,
        ) as mocked:
            feedback._run_closed_loop()
        self.assertTrue(mocked.called)
        self.assertFalse(feedback.helpdesk_ticket_id)
        self.assertTrue(
            feedback.activity_ids,
            "the core closed loop must still have scheduled its activity",
        )
