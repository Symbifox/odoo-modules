from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestOnboardingStateMachine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Client SARL",
            "email": "test-client@example.com",
        })
        cls.project = cls.env["project.project"].create({
            "name": "Test Onboarding Project",
            "partner_id": cls.partner.id,
        })

    def test_initial_state_is_not_started(self):
        self.assertEqual(self.project.onboarding_state, "not_started")
        self.assertEqual(self.project.onboarding_progress, 0.0)

    def test_progress_increases_with_state(self):
        steps = [
            ("not_started", 0.0),
            ("nda_pending", 100.0 / 6),
            ("nda_signed", 200.0 / 6),
            ("intake_pending", 300.0 / 6),
            ("intake_completed", 400.0 / 6),
            ("kickoff_done", 500.0 / 6),
            ("active", 100.0),
        ]
        for state, expected in steps:
            self.project.onboarding_state = state
            self.assertAlmostEqual(
                self.project.onboarding_progress, expected, places=3,
                msg=f"Wrong progress for state {state}",
            )

    def test_send_nda_creates_letter_and_advances(self):
        action = self.project.action_send_nda()
        self.assertEqual(self.project.onboarding_state, "nda_pending")
        self.assertTrue(self.project.nda_letter_document_id)
        self.assertEqual(action["res_model"], "letter.document")

    def test_send_nda_blocked_from_wrong_state(self):
        self.project.onboarding_state = "nda_signed"
        with self.assertRaises(UserError):
            self.project.action_send_nda()

    def test_mark_nda_signed_sets_date_and_advances(self):
        self.project.action_send_nda()
        self.project.action_mark_nda_signed()
        self.assertEqual(self.project.onboarding_state, "nda_signed")
        self.assertEqual(self.project.nda_signed_date, fields.Date.context_today(self.project))

    def test_intake_flow_creates_response(self):
        self.project.action_send_nda()
        self.project.action_mark_nda_signed()
        self.project.action_send_intake()
        self.assertEqual(self.project.onboarding_state, "intake_pending")
        self.assertTrue(self.project.intake_survey_response_id)
        self.assertEqual(
            self.project.intake_survey_response_id.partner_id, self.partner
        )

    def test_intake_completion_auto_advances(self):
        self.project.action_send_nda()
        self.project.action_mark_nda_signed()
        self.project.action_send_intake()
        # Simulate the participant finishing the survey.
        self.project.intake_survey_response_id._mark_done()
        self.assertEqual(self.project.onboarding_state, "intake_completed")
        self.assertTrue(self.project.intake_completed_date)

    def test_kickoff_requires_meeting(self):
        self.project.onboarding_state = "intake_completed"
        with self.assertRaises(UserError):
            self.project.action_mark_kickoff_done()
        # Create a meeting then retry.
        meeting = self.env["meeting.record"].create({
            "project_id": self.project.id,
            "date": fields.Datetime.now(),
        })
        self.project.action_mark_kickoff_done()
        self.assertEqual(self.project.onboarding_state, "kickoff_done")
        self.assertEqual(self.project.kickoff_meeting_id, meeting)

    def test_activate_only_from_kickoff_or_hold(self):
        self.project.onboarding_state = "kickoff_done"
        self.project.action_activate()
        self.assertEqual(self.project.onboarding_state, "active")

    def test_block_then_unblock_resumes_at_milestone(self):
        # Walk through up to nda_signed then block.
        self.project.action_send_nda()
        self.project.action_mark_nda_signed()
        self.project.action_send_intake()
        self.project.action_block()
        self.assertEqual(self.project.onboarding_state, "on_hold")
        # Unblock: should resume at intake_pending (response exists, no completion).
        self.project.action_unblock()
        self.assertEqual(self.project.onboarding_state, "intake_pending")

    def test_state_change_logs_timestamp(self):
        before = fields.Datetime.now()
        self.project.action_send_nda()
        self.assertGreaterEqual(self.project.onboarding_state_last_change, before)
