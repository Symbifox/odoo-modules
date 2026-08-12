from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_anonymization_assessment")
class TestPrivacyAnonymizationAssessment(TransactionCase):
    """Test cases for anonymization assessments (Règl. A-2.1, r. 0.1)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Assessment = cls.env["privacy.anonymization.assessment"]
        cls.officer_group = cls.env.ref("privacy_consent.group_privacy_officer")
        cls.officer = cls.env["res.users"].create({
            "name": "Anon Officer",
            "login": "test_anon_officer",
            "email": "anon-officer@example.com",
            "groups_id": [(4, cls.officer_group.id)],
        })

    def _base_vals(self, **overrides):
        vals = {
            "name": "Test assessment",
            "dataset_description": "Test dataset",
        }
        vals.update(overrides)
        return vals

    def test_default_state_is_draft(self):
        """New assessment starts in draft."""
        a = self.Assessment.create(self._base_vals())
        self.assertEqual(a.state, "draft")

    def test_full_workflow(self):
        """draft → analysis → completed → approved."""
        a = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="low",
            inference_risk="low",
        ))
        a.action_start_analysis()
        self.assertEqual(a.state, "analysis")

        a.action_complete()
        self.assertEqual(a.state, "completed")
        self.assertTrue(a.assessment_date)

        a.with_user(self.officer).action_approve()
        self.assertEqual(a.approved_by_id, self.officer)
        self.assertTrue(a.approval_date)

    def test_complete_requires_all_three_criteria(self):
        """Cannot complete if any of the 3 risks is unset."""
        a = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="low",
            # inference_risk unset
        ))
        a.action_start_analysis()
        with self.assertRaises(UserError):
            a.action_complete()

    def test_overall_risk_is_max(self):
        """overall_risk = max of 3 criteria."""
        a = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="medium",
            inference_risk="high",
        ))
        self.assertEqual(a.overall_risk, "high")

        a.inference_risk = "medium"
        self.assertEqual(a.overall_risk, "medium")

    def test_is_effectively_anonymous_only_if_all_low(self):
        """is_effectively_anonymous True only if all three are low."""
        a = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="low",
            inference_risk="low",
        ))
        self.assertTrue(a.is_effectively_anonymous)

        a.inference_risk = "medium"
        self.assertFalse(a.is_effectively_anonymous)

    def test_approve_requires_officer(self):
        """Non-officer cannot approve."""
        user_group = self.env.ref("privacy_consent.group_privacy_user")
        plain = self.env["res.users"].create({
            "name": "Plain user",
            "login": "test_anon_plain",
            "email": "anon-plain@example.com",
            "groups_id": [(4, user_group.id)],
        })
        a = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="low",
            inference_risk="low",
        ))
        a.action_start_analysis()
        a.action_complete()
        with self.assertRaises(UserError):
            a.with_user(plain).action_approve()

    def test_approve_requires_completed(self):
        """Cannot approve a draft / analysis-state assessment."""
        a = self.Assessment.create(self._base_vals())
        with self.assertRaises(UserError):
            a.with_user(self.officer).action_approve()

    def test_reassessment_chain(self):
        """action_create_reassessment links the new assessment to the parent."""
        parent = self.Assessment.create(self._base_vals(
            individualization_risk="low",
            correlation_risk="low",
            inference_risk="low",
        ))
        parent.action_start_analysis()
        parent.action_complete()
        parent.with_user(self.officer).action_approve()

        action = parent.action_create_reassessment()
        new_id = action["res_id"]
        new = self.Assessment.browse(new_id)
        self.assertEqual(new.parent_assessment_id, parent)
        self.assertEqual(new.state, "draft")
        self.assertIn(new, parent.child_assessment_ids)

    def test_invalid_state_transition_blocked(self):
        """Direct write to skip states raises UserError."""
        a = self.Assessment.create(self._base_vals())
        with self.assertRaises(UserError):
            a.write({"state": "completed"})
