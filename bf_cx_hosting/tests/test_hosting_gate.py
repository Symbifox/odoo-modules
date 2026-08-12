"""Post-maintenance CSAT: the switch, the internal-service exclusion.

Maintenance schedules are recurring and run monthly on dozens of client
services, so this is the bridge with the widest blast radius if its
switch ever defaulted on. The internal exclusion matters just as much:
Blue Fox hosts services for itself, and those must never trigger a
client survey addressed to the company's own partner.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestHostingFeedbackGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.software = cls.env["hosting.software"].create(
            {
                "name": "Logiciel pont CX",
                "code": "cx_bridge_test",
                "software_type": "self_hosted",
            }
        )
        cls.service = cls.env["hosting.service"].create(
            {
                "name": "Service pont CX",
                "partner_id": cls.cx_partner.id,
                "software_id": cls.software.id,
                "state": "active",
                "environment": "production",
                "version_policy": "latest",
            }
        )
        cls.schedule = cls.env["hosting.maintenance.schedule"].create(
            {
                "name": "Maintenance pont CX",
                "service_id": cls.service.id,
                "frequency": "monthly",
                "maintenance_type": "security_patch",
            }
        )

    def test_gate_off_sends_nothing(self):
        self.set_gate("bf_cx.hosting_feedback", False)
        self.schedule._bf_cx_maybe_request_feedback()
        self.assertNothingSent(self.schedule)
        self.assertFalse(self.schedule.bf_cx_feedback_sent)

    def test_gate_on_asks_once_per_cycle(self):
        self.set_gate("bf_cx.hosting_feedback", True)
        self.schedule._bf_cx_maybe_request_feedback()
        self.assertEqual(len(self.ratings_of(self.schedule)), 1)
        self.assertTrue(self.schedule.bf_cx_feedback_sent)
        # Same cycle, second call: the per-cycle flag holds.
        self.schedule._bf_cx_maybe_request_feedback()
        self.assertEqual(len(self.ratings_of(self.schedule)), 1)

    def test_internal_service_never_surveyed(self):
        """A service the company hosts for itself must not be surveyed."""
        self.set_gate("bf_cx.hosting_feedback", True)
        company_partner = self.env.company.partner_id
        company_partner.email = (
            company_partner.email or "compagnie-cx@example.com"
        )
        self.service.partner_id = company_partner
        self.schedule.invalidate_recordset(["partner_id"])
        self.schedule._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.schedule))
        self.assertFalse(self.schedule.bf_cx_feedback_sent)

    def test_cooldown_blocks_and_leaves_a_trace(self):
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_gate("bf_cx.hosting_feedback", True)
        self.cx_partner._bf_cx_mark_solicited()
        before = len(self.schedule.message_ids)
        self.schedule._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.schedule))
        self.assertGreater(len(self.schedule.message_ids), before)

    def test_marking_done_survives_a_broken_hook(self):
        self.assertHookIsolated(
            self.schedule,
            "_bf_cx_maybe_request_feedback",
            lambda: self.schedule.action_mark_done(),
        )
        self.assertTrue(
            self.schedule.last_performed,
            "the maintenance must still be recorded as done",
        )
