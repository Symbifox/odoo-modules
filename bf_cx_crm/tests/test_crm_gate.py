"""Win/loss rails: both switches are program-valued, so empty means off.

The loss rail mails a survey immediately; the won rail must never mail
anything - it only parks the fresh customer in a draft wave. Both hooks
sit on top of core state methods (action_set_lost / action_set_won), so
they also have to be unable to break losing or winning a deal.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestCrmGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.lead = cls.env["crm.lead"].create(
            {
                "name": "Occasion pont CX",
                "partner_id": cls.cx_partner.id,
                "type": "opportunity",
            }
        )

    # ------------------------------------------------------------------
    # Loss rail
    # ------------------------------------------------------------------
    def test_loss_gate_empty_sends_nothing(self):
        self.set_program_gate("bf_cx.loss_program_id", None)
        before = len(self.answers_of(self.program))
        self.lead._bf_cx_send_loss_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)
        self.assertFalse(self.lead.bf_cx_loss_survey_sent)
        self.assertFalse(self.cx_partner.bf_cx_last_solicited)

    def test_loss_gate_garbage_value_sends_nothing(self):
        """A non-numeric parameter must read as off, never crash the loss."""
        self.Param.set_param("bf_cx.loss_program_id", "oups")
        before = len(self.answers_of(self.program))
        self.lead._bf_cx_send_loss_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)

    def test_loss_gate_on_surveys_once(self):
        self.set_program_gate("bf_cx.loss_program_id", self.program)
        before = len(self.answers_of(self.program))
        self.lead._bf_cx_send_loss_survey()
        self.assertEqual(len(self.answers_of(self.program)), before + 1)
        self.assertTrue(self.lead.bf_cx_loss_survey_sent)
        self.assertTrue(self.cx_partner.bf_cx_last_solicited)
        # A second pass over the same lead must not survey again.
        self.lead._bf_cx_send_loss_survey()
        self.assertEqual(len(self.answers_of(self.program)), before + 1)

    def test_loss_cooldown_blocks(self):
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "30")
        self.set_program_gate("bf_cx.loss_program_id", self.program)
        self.cx_partner._bf_cx_mark_solicited()
        before = len(self.answers_of(self.program))
        self.lead._bf_cx_send_loss_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)
        self.assertFalse(self.lead.bf_cx_loss_survey_sent)

    def test_losing_a_deal_survives_a_broken_hook(self):
        self.assertHookIsolated(
            self.lead,
            "_bf_cx_send_loss_survey",
            lambda: self.lead.action_set_lost(),
        )
        self.assertFalse(
            self.lead.active, "the deal must still be marked lost"
        )

    # ------------------------------------------------------------------
    # Won rail
    # ------------------------------------------------------------------
    def test_won_gate_empty_enrolls_nobody(self):
        self.set_program_gate("bf_cx.won_program_id", None)
        self.lead._bf_cx_enroll_won()
        self.assertFalse(
            self.env["bf.cx.wave"].search(
                [("partner_ids", "in", self.cx_partner.ids)]
            )
        )

    def test_won_enrollment_never_mails(self):
        """Signing must enrol, not solicit: no send, no cooldown stamp."""
        self.set_program_gate("bf_cx.won_program_id", self.program)
        before = len(self.answers_of(self.program))
        self.lead._bf_cx_enroll_won()
        wave = self.env["bf.cx.wave"].search(
            [
                ("program_id", "=", self.program.id),
                ("state", "=", "draft"),
                ("partner_ids", "in", self.cx_partner.ids),
            ]
        )
        self.assertTrue(wave, "the fresh customer must land in a draft wave")
        self.assertEqual(
            len(self.answers_of(self.program)),
            before,
            "enrolling must not send anything on day one",
        )
        self.assertFalse(self.cx_partner.bf_cx_last_solicited)
        self.assertFalse(self.mails_to(self.cx_partner))

    def test_won_enrollment_is_idempotent(self):
        self.set_program_gate("bf_cx.won_program_id", self.program)
        self.lead._bf_cx_enroll_won()
        self.lead._bf_cx_enroll_won()
        waves = self.env["bf.cx.wave"].search(
            [
                ("program_id", "=", self.program.id),
                ("state", "=", "draft"),
                ("partner_ids", "in", self.cx_partner.ids),
            ]
        )
        self.assertEqual(len(waves), 1)
        self.assertEqual(
            len(waves.partner_ids.filtered(lambda p: p == self.cx_partner)),
            1,
        )
