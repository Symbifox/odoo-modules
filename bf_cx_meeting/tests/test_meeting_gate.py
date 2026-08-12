"""Post-report feedback: the switch, the cooldown and the resend guard.

bf_cx.meeting_feedback is off in production on purpose. These tests pin
the three ways a client could be mailed without meaning to: the switch
silently defaulting to on, the anti-oversolicitation cooldown being
bypassed, and a resent report asking a second time.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestMeetingFeedbackGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create(
            {"name": "Projet pont CX", "partner_id": cls.cx_partner.id}
        )
        cls.meeting = cls.env["meeting.record"].create(
            {
                "name": "Rencontre pont CX",
                "project_id": cls.project.id,
                "date": "2026-07-20 14:00:00",
                "meeting_type": "video",
            }
        )

    def test_gate_off_sends_nothing(self):
        self.set_gate("bf_cx.meeting_feedback", False)
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertNothingSent(self.meeting)
        self.assertFalse(self.meeting.bf_cx_feedback_requested)

    def test_absent_parameter_is_off(self):
        """An unset switch must read as off, not as a falsy-but-on value.

        get_param returns False (not None) for an absent key, which is
        exactly the trap param_is_true() exists to avoid.
        """
        self.Param.search([("key", "=", "bf_cx.meeting_feedback")]).unlink()
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertNothingSent(self.meeting)

    def test_gate_on_asks_once(self):
        self.set_gate("bf_cx.meeting_feedback", True)
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertEqual(len(self.ratings_of(self.meeting)), 1)
        self.assertTrue(self.meeting.bf_cx_feedback_requested)
        self.assertTrue(self.cx_partner.bf_cx_last_solicited)
        # Resending the report must not ask the same client twice.
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertEqual(len(self.ratings_of(self.meeting)), 1)

    def test_cooldown_blocks_and_leaves_a_trace(self):
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_gate("bf_cx.meeting_feedback", True)
        self.cx_partner._bf_cx_mark_solicited()
        before = len(self.meeting.message_ids)
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.meeting))
        self.assertFalse(
            self.meeting.bf_cx_feedback_requested,
            "a blocked ask must stay askable once the cooldown expires",
        )
        self.assertGreater(
            len(self.meeting.message_ids),
            before,
            "a blocked ask must be traceable in the chatter",
        )

    def test_contact_without_email_is_skipped(self):
        self.set_gate("bf_cx.meeting_feedback", True)
        self.cx_partner.email = False
        self.meeting._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.meeting))
