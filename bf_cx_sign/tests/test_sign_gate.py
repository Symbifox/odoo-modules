"""Post-signature feedback: the switch and the signer resolution.

bf.sign.request has no partner_id - signers live on bf.sign.signer - so
the bridge resolves the main signer itself. If that resolution silently
returned the wrong record, the rating token and the mail would target
the wrong person: worth pinning as tightly as the switch.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestSignFeedbackGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.second_partner = cls.env["res.partner"].create(
            {"name": "Cosignataire CX", "email": "cosign-cx@example.com"}
        )
        cls.request = cls.env["bf.sign.request"].create(
            {
                "name": "Entente pont CX",
                "signature_method": "native_ses",
                "signing_order": "parallel",
            }
        )
        cls.env["bf.sign.signer"].create(
            {
                "request_id": cls.request.id,
                "name": cls.cx_partner.name,
                "email": cls.cx_partner.email,
                "partner_id": cls.cx_partner.id,
                "sequence": 1,
            }
        )
        cls.env["bf.sign.signer"].create(
            {
                "request_id": cls.request.id,
                "name": cls.second_partner.name,
                "email": cls.second_partner.email,
                "partner_id": cls.second_partner.id,
                "sequence": 2,
            }
        )
        cls.request.state = "signed"

    def test_main_signer_is_the_lowest_sequence(self):
        self.assertEqual(self.request._rating_get_partner(), self.cx_partner)

    def test_gate_off_sends_nothing(self):
        self.set_gate("bf_cx.sign_feedback", False)
        self.request._bf_cx_maybe_request_feedback()
        self.assertNothingSent(self.request)
        self.assertFalse(self.request.bf_cx_feedback_sent)

    def test_unsigned_request_is_never_surveyed(self):
        self.set_gate("bf_cx.sign_feedback", True)
        self.request.state = "in_progress"
        self.request._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.request))

    def test_gate_on_asks_the_main_signer_once(self):
        self.set_gate("bf_cx.sign_feedback", True)
        self.request._bf_cx_maybe_request_feedback()
        ratings = self.ratings_of(self.request)
        self.assertEqual(len(ratings), 1)
        self.assertEqual(ratings.partner_id, self.cx_partner)
        self.assertTrue(self.request.bf_cx_feedback_sent)
        self.request._bf_cx_maybe_request_feedback()
        self.assertEqual(len(self.ratings_of(self.request)), 1)

    def test_cooldown_blocks_and_leaves_a_trace(self):
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_gate("bf_cx.sign_feedback", True)
        self.cx_partner._bf_cx_mark_solicited()
        before = len(self.request.message_ids)
        self.request._bf_cx_maybe_request_feedback()
        self.assertFalse(self.ratings_of(self.request))
        self.assertFalse(self.request.bf_cx_feedback_sent)
        self.assertGreater(len(self.request.message_ids), before)
