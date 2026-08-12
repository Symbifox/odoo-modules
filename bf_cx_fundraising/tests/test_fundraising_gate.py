"""Donor survey: the switch, the state guard and the program cadence.

A loyal donor gives often, so the per-program cadence - not the global
30-day cooldown - is what protects them here. The state guard matters
too: a draft donation is not a gift yet, and thanking someone for it
would be embarrassing.
"""
from odoo.tests import tagged

from odoo.addons.bf_cx.tests.common import CxBridgeCase


@tagged("post_install", "-at_install")
class TestDonorSurveyGate(CxBridgeCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.program = cls.env.ref("bf_cx.program_nps_default")
        cls.donation = cls.env["donation.donation"].create(
            {
                "partner_id": cls.cx_partner.id,
                "donation_date": "2026-07-20",
                "currency_id": cls.env.company.currency_id.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.donation.state = "done"

    def test_gate_empty_sends_nothing(self):
        self.set_program_gate("bf_cx.donor_program_id", None)
        before = len(self.answers_of(self.program))
        self.donation._bf_cx_send_donor_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)
        self.assertFalse(self.donation.bf_cx_donor_survey_sent)
        self.assertFalse(self.cx_partner.bf_cx_last_solicited)

    def test_draft_donation_is_never_surveyed(self):
        self.set_program_gate("bf_cx.donor_program_id", self.program)
        self.donation.state = "draft"
        before = len(self.answers_of(self.program))
        self.donation._bf_cx_send_donor_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)

    def test_gate_on_surveys_once(self):
        self.set_program_gate("bf_cx.donor_program_id", self.program)
        before = len(self.answers_of(self.program))
        self.donation._bf_cx_send_donor_survey()
        self.assertEqual(len(self.answers_of(self.program)), before + 1)
        self.assertTrue(self.donation.bf_cx_donor_survey_sent)
        self.donation._bf_cx_send_donor_survey()
        self.assertEqual(len(self.answers_of(self.program)), before + 1)

    def test_program_cadence_overrides_a_disabled_global_cooldown(self):
        """cooldown_days on the program must win over a global 0.

        The global cooldown is the floor, not the ceiling: a donor
        program set to 90 days has to hold even when the instance-wide
        parameter is relaxed.
        """
        self.Param.set_param("bf_cx.solicitation_cooldown_days", "0")
        self.program.cooldown_days = 90
        self.set_program_gate("bf_cx.donor_program_id", self.program)
        self.cx_partner._bf_cx_mark_solicited()
        before = len(self.answers_of(self.program))
        self.donation._bf_cx_send_donor_survey()
        self.assertEqual(len(self.answers_of(self.program)), before)
        self.assertFalse(self.donation.bf_cx_donor_survey_sent)
